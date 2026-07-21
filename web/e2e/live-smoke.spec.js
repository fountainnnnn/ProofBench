// Opt-in live smoke test. THIS SPENDS REAL MONEY.
//
// It is skipped unless PROOFBENCH_RUN_LIVE_SMOKE=1 is set explicitly.
//
// PROOFBENCH_E2E_TOKEN is optional and selects the deployment profile:
//   unset -> the local tokenless Compose profile (PROOFBENCH_INSECURE_DEV=1);
//            requests carry no Authorization header.
//   set   -> an authenticated deployment; every request carries that bearer.
//
// It exercises the real path end to end: the configured OpenAI intake turns a
// short objective into a spec, Daytona executes the candidates, and the run
// persists immutable `measured` results plus a downloadable PDF.
//
// Cost is held down deliberately:
//   - one sample labelled dataset (15 synthetic invoice images, generated locally)
//   - one short intake message (the only hosted-inference call)
//   - candidates narrowed to ProofBench's first-party LOCAL tools (tesseract,
//     easyocr), so no paid per-image hosted candidate inference is billed
//   - a strict wall-clock deadline that aborts rather than polling forever
//
// Nothing here logs the token, provider keys, or response headers.
import { expect, test } from "@playwright/test";
import { guarded } from "./liveSmokeRequest.js";

const token = process.env.PROOFBENCH_E2E_TOKEN || "";
const enabled = process.env.PROOFBENCH_RUN_LIVE_SMOKE === "1";
// Empty in the local tokenless profile, so no placeholder credential is sent.
// The transport-error firewall in liveSmokeRequest.js still applies either way.
const AUTH_HEADERS = token ? { Authorization: `Bearer ${token}` } : {};

// Whole-test budget. Intake is usually seconds; Daytona sandbox provisioning
// plus two local OCR candidates over 15 images dominates the rest.
const BUDGET_MS = Number(process.env.PROOFBENCH_LIVE_SMOKE_TIMEOUT_MS || 15 * 60 * 1000);
const INTAKE_MS = 90_000;
// Cleanup runs after the budget is already spent, so it needs its own bounded
// slice of the timeout. Anything longer is abandoned; a stuck stop request must
// not be what makes the suite hang.
const CLEANUP_MS = 20_000;

// Never let a value that could embed the credential reach a failure message or
// the console. Applied to anything derived from a response body.
function safeDetail(value) {
  const text = String(value ?? "");
  const redacted = token ? text.split(token).join("[redacted]") : text;
  return redacted.replace(/[^\x20-\x7e]/g, " ").slice(0, 200);
}

// Local, first-party candidates only. Both run inside the Daytona sandbox and
// bill no hosted inference, which is what keeps this smoke test cheap.
const LOCAL_CANDIDATES = [
  { name: "tesseract", kind: "local_tool", use_fallback: true },
  { name: "easyocr", kind: "local_tool", use_fallback: true },
];

const FIELDS = ["invoice_number", "date", "vendor", "total"];

// Every status a run can settle into server-side (server/state.py). Once the
// run reports one of these it will not change again, so polling stops there
// whether the verdict is success or not.
const TERMINAL_STATUSES = ["completed", "failed", "stopped"];

// Belt and braces with the live-smoke project in playwright.config.js: every
// request here carries a bearer token, and trace/video/screenshot artifacts
// would persist it to disk on failure. These options force a worker, so
// Playwright only accepts them at the top level of the file.
test.use({ trace: "off", video: "off", screenshot: "off" });

test.describe("live smoke", () => {
  test.skip(
    !enabled,
    "Set PROOFBENCH_RUN_LIVE_SMOKE=1 to run the live smoke test (PROOFBENCH_E2E_TOKEN only for authenticated deployments). It calls real providers and incurs cost.",
  );

  test("a real run produces measured provenance, metrics, and a PDF", async ({ request }) => {
    // Budget for the run, a bounded slice for cleanup, and 60s of headroom
    // beyond both so an aborting test still gets to stop what it started.
    test.setTimeout(BUDGET_MS + CLEANUP_MS + 60_000);
    const headers = AUTH_HEADERS;
    const deadline = Date.now() + BUDGET_MS;
    const remaining = (label) => {
      const left = deadline - Date.now();
      if (left <= 0) throw new Error(`live smoke budget of ${BUDGET_MS}ms exhausted while waiting for ${label}`);
      return left;
    };

    // Anything the finally block needs to decide whether cleanup is owed.
    let sessionId = "";
    let terminal = false;

    try {
      // Fail fast and cheaply if the deployment is not configured for real runs,
      // rather than discovering it after paying for intake. Config check only.
      const readinessResponse = await guarded("provider readiness request failed", () =>
        request.get("/api/providers", { headers }),
      );
      expect(readinessResponse.ok(), "provider readiness unavailable").toBeTruthy();
      const readiness = await guarded("provider readiness response was unreadable", () =>
        readinessResponse.json(),
      );
      expect(
        readiness.run_ready,
        `blocked_by: ${safeDetail((readiness.blocked_by || []).join(", "))}`,
      ).toBeTruthy();

      // 1. Sample labelled dataset. Synthetic INPUT images with known ground
      //    truth; the metrics measured against them are genuine.
      const datasetResponse = await guarded("dataset request failed", () =>
        request.post("/api/datasets", { headers, data: { use_synthetic: true } }),
      );
      expect(datasetResponse.ok(), "dataset generation failed").toBeTruthy();
      const { dataset_id: datasetId } = await guarded("dataset response was unreadable", () =>
        datasetResponse.json(),
      );

      // 2. One short real extraction objective through the configured OpenAI intake.
      const chatResponse = await guarded("intake request failed", () =>
        request.post("/api/chat", {
          headers,
          data: {
            message: "Benchmark tesseract and easyocr on invoice number, date, vendor, and total.",
            dataset_id: datasetId,
          },
        }),
      );
      expect(chatResponse.ok(), "intake rejected the objective").toBeTruthy();
      // Assigned before any further assertion can throw, so cleanup always
      // knows about a session that exists server-side.
      ({ session_id: sessionId } = await guarded("intake response was unreadable", () =>
        chatResponse.json(),
      ));
      expect(sessionId, "intake returned no session id").toBeTruthy();

      let session;
      await expect
        .poll(async () => {
          const response = await guarded("session request failed", () =>
            request.get(`/api/sessions/${sessionId}`, { headers }),
          );
          if (!response.ok()) return false;
          session = await guarded("session response was unreadable", () => response.json());
          return Boolean(session.spec);
        }, { timeout: Math.min(INTAKE_MS, remaining("intake to produce a spec")), intervals: [1000] })
        .toBe(true);

      // 3. Pin the run to the minimum viable local candidates. Whatever intake
      //    proposed, this is what actually executes, so cost stays bounded.
      const spec = {
        benchmark_type: "extraction",
        category: session.spec.category || "invoice",
        fields: FIELDS,
        candidates: LOCAL_CANDIDATES,
        dataset: { dataset_id: datasetId },
      };
      const runResponse = await guarded("run request failed", () =>
        request.post(`/api/sessions/${sessionId}/run`, { headers, data: { spec } }),
      );
      expect(runResponse.ok(), "run was not accepted").toBeTruthy();
      const { run_id: runId } = await guarded("run response was unreadable", () =>
        runResponse.json(),
      );
      expect(runId).toMatch(/^[a-f0-9]{12}$/);

      // 4. Wait for a terminal state within the remaining budget. Poll for ANY
      //    terminal status, not for "completed": a run that already failed will
      //    never change again, so waiting for success would burn the whole
      //    budget to report a verdict the server had already given us.
      let results;
      await expect
        .poll(async () => {
          const response = await guarded("results request failed", () =>
            request.get(`/api/runs/${runId}/results`, { headers }),
          );
          if (!response.ok()) return false;
          results = await guarded("results response was unreadable", () => response.json());
          return TERMINAL_STATUSES.includes(results.status);
        }, { timeout: remaining("the run to finish"), intervals: [5000] })
        .toBe(true);
      // The run reached a terminal state under its own power. Later assertions
      // may still fail, but there is nothing left running to stop.
      terminal = true;

      // Now judge the verdict. The message is fixed and derived only from the
      // status enum, so nothing from the response body can carry a secret.
      expect(
        results.status,
        `run reached terminal status "${safeDetail(results.status)}" instead of completed`,
      ).toBe("completed");

      // 5. Immutable, genuinely measured results.
      expect(results.provenance, "a real run must persist measured provenance").toBe("measured");
      expect(results.metrics, "measured runs must carry metrics").toBeTruthy();
      expect(Object.keys(results.metrics).length).toBeGreaterThan(0);
      expect(results.run_id).toBe(runId);

      // Provenance is immutable: re-reading returns the same measured verdict.
      const rereadResponse = await guarded("results reread request failed", () =>
        request.get(`/api/runs/${runId}/results`, { headers }),
      );
      expect(rereadResponse.ok()).toBeTruthy();
      const reread = await guarded("results reread response was unreadable", () =>
        rereadResponse.json(),
      );
      expect(reread.provenance).toBe("measured");
      expect(reread.run_id).toBe(runId);

      // 6. The report PDF is downloadable.
      const reportResponse = await guarded("report request failed", () =>
        request.get(`/api/runs/${runId}/report.pdf`, { headers }),
      );
      expect(reportResponse.ok(), "report PDF was not downloadable").toBeTruthy();
      expect(reportResponse.headers()["content-type"]).toContain("application/pdf");
      const reportBody = await guarded("report body was unreadable", () => reportResponse.body());
      expect(reportBody.byteLength).toBeGreaterThan(1000);
    } finally {
      // A session that never reached a terminal state may still hold a Daytona
      // sandbox and keep spending. Stopping is idempotent server-side and is
      // strictly best effort: it is bounded by CLEANUP_MS, never throws, and
      // never reports anything beyond a status code.
      if (sessionId && !terminal) {
        try {
          // Guarded like every other authenticated call, so a transport failure
          // here raises the fixed label rather than a call log carrying the
          // bearer token. The catch then discards even that label.
          const stopResponse = await guarded("cleanup stop request failed", () =>
            request.post(`/api/sessions/${encodeURIComponent(sessionId)}/stop`, {
              headers,
              data: {},
              timeout: CLEANUP_MS,
              failOnStatusCode: false,
            }),
          );
          console.log(`live smoke cleanup: stop returned HTTP ${stopResponse.status()}`);
        } catch {
          // No binding: nothing derived from the failure reaches the console.
          console.log("live smoke cleanup: stop did not complete");
        }
      }
    }
  });
});
