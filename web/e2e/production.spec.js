import { expect, test } from "@playwright/test";
import { createRequire } from "node:module";
import { guarded } from "./liveSmokeRequest.js";

const require = createRequire(import.meta.url);
const axePath = require.resolve("axe-core/axe.min.js");
// The token is what selects the deployment profile under test. Against the
// default local Compose (PROOFBENCH_INSECURE_DEV=1) there is none: the console
// enters without a gate and API requests carry no Authorization header. Set
// PROOFBENCH_E2E_TOKEN to point the same spec at an authenticated deployment.
const token = process.env.PROOFBENCH_E2E_TOKEN || "";
const authenticated = token.length > 0;
const authHeaders = authenticated ? { Authorization: `Bearer ${token}` } : {};

async function assertA11y(page, label) {
  await page.addScriptTag({ path: axePath });
  const violations = await page.evaluate(async () => {
    const result = await window.axe.run(document, {
      runOnly: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "best-practice"],
    });
    return result.violations.map(({ id, impact, nodes }) => ({
      id,
      impact,
      targets: nodes.slice(0, 5).map((node) => node.target),
    }));
  });
  expect(violations, `${label} accessibility violations`).toEqual([]);
}

// ProofBench is real-only: the console must not ship any control that starts or
// selects a demo/synthetic execution. Marketing copy on the unauthenticated
// landing page is out of scope, so this only inspects the signed-in console.
async function assertNoDemoExecutionControl(page, label) {
  const controls = await page.locator("#proofbench-main").evaluate((root) => {
    const selector = 'button, a, input, select, [role="button"], [role="switch"], [role="radio"], [role="checkbox"], [role="tab"], [role="menuitem"]';
    return Array.from(root.querySelectorAll(selector))
      .map((node) => [
        node.textContent || "",
        node.getAttribute("aria-label") || "",
        node.getAttribute("name") || "",
        node.getAttribute("value") || "",
        node.id || "",
      ].join(" "))
      .filter((text) => /\bdemo\b/i.test(text));
  });
  expect(controls, `${label} must expose no demo execution control`).toEqual([]);
}

async function signIn(page, path = "/app/datasets") {
  await page.goto(path);
  const consoleHeading = page.getByRole("heading", { name: /Datasets|New benchmark|Runs|Settings/ }).first();
  if (authenticated) {
    const input = page.locator("#proofbench-token");
    await expect.poll(async () => (
      (await input.isVisible().catch(() => false)) || (await consoleHeading.isVisible().catch(() => false))
    )).toBe(true);
    if (await input.isVisible()) {
      await input.fill(token);
      await page.getByRole("button", { name: /Sign in|Restore write access/ }).click();
    }
  }
  await expect(consoleHeading).toBeVisible();
}

test("production shell, dataset lifecycle, accessibility, and responsive layout", async ({ page }) => {
  const documentResponse = await page.goto("/app/datasets");
  expect(documentResponse.headers()["content-security-policy"]).toContain("default-src 'self'");

  if (authenticated) {
    await expect(page.getByRole("heading", { name: "Sign in to ProofBench" })).toBeVisible();
    await assertA11y(page, "sign-in");

    await page.getByLabel("Password").fill(token);
    await page.getByRole("button", { name: "Sign in" }).click();
  }
  await expect(page.locator("#proofbench-main").getByRole("heading", { name: "Datasets" })).toBeVisible();
  await assertA11y(page, "datasets-empty");

  if (authenticated) {
    await page.reload();
    await expect(page.getByRole("heading", { name: "Re-enter your password" })).toBeVisible();
    await expect(page.locator("#proofbench-main")).toHaveCount(0);
    await page.locator("#proofbench-token").fill(token);
    await page.getByRole("button", { name: "Restore write access" }).click();
  } else {
    // Local tokenless mode: a reload must land straight back in the console,
    // with no token gate and no sign-out control anywhere in the shell.
    await page.reload();
    await expect(page.locator("#proofbench-token")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Sign out" })).toHaveCount(0);
    await expect(page.getByText("Local mode")).toBeVisible();
  }
  await expect(page.locator("#proofbench-main").getByRole("heading", { name: "Datasets" })).toBeVisible();

  await expect(page.getByRole("heading", { name: "Sample labelled dataset" })).toBeVisible();
  await page.getByRole("button", { name: "Generate sample dataset" }).click();
  await expect(page.getByRole("button", { name: "Use in benchmark" }).first()).toBeVisible({ timeout: 20_000 });
  await assertNoDemoExecutionControl(page, "datasets");
  const desktopOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(desktopOverflow).toBeLessThanOrEqual(1);
  await assertA11y(page, "datasets-populated");

  await page.getByRole("link", { name: "Benchmark" }).click();
  await expect(page.locator("#proofbench-main").getByRole("button", { name: "New benchmark" }).first()).toBeVisible();
  await assertNoDemoExecutionControl(page, "benchmark");

  await page.getByRole("link", { name: "Runs" }).click();
  await expect(page.locator("#proofbench-main").getByRole("heading", { name: "Runs" })).toBeVisible();
  await assertNoDemoExecutionControl(page, "runs");
  await assertA11y(page, "runs");

  await page.getByRole("link", { name: "Settings" }).click();
  await expect(page.locator("#proofbench-main").getByRole("heading", { name: "Settings" })).toBeVisible();
  await assertA11y(page, "settings");

  await page.setViewportSize({ width: 390, height: 844 });
  await signIn(page, "/app/datasets");
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
  await assertA11y(page, "datasets-mobile");

  if (authenticated) {
    await page.getByRole("button", { name: "Sign out" }).click();
    await expect(page.getByRole("heading", { name: "Sign in to ProofBench" })).toBeVisible();
  }
});

// Deterministic, provider-free: every request below is either rejected at the
// schema boundary or served from local storage, so this test makes zero live
// provider calls and allocates no sessions or runs beyond the one it creates
// explicitly through the provider-free POST /api/sessions endpoint.
test("the production API rejects explicit demo mode without allocating a session or run", async ({ request }) => {
  // Empty in local tokenless mode, so these requests send no Authorization
  // header at all rather than a placeholder one.
  const headers = authHeaders;

  const datasetResponse = await guarded("dataset request failed", () =>
    request.post("/api/datasets", {
      headers,
      data: { use_synthetic: true },
    }),
  );
  expect(datasetResponse.ok(), await datasetResponse.text()).toBeTruthy();
  expect(datasetResponse.headers()["cache-control"]).toContain("no-store");
  const { dataset_id: datasetId } = await datasetResponse.json();

  const listSessionIds = async () => {
    const response = await guarded("session list request failed", () =>
      request.get("/api/sessions", { headers }),
    );
    expect(response.ok(), await response.text()).toBeTruthy();
    return (await response.json()).map((session) => session.id).sort();
  };

  // 1. Explicit mode:"demo" on /api/chat is a 422 and creates no session.
  const sessionsBefore = await listSessionIds();
  const chatResponse = await guarded("chat rejection request failed", () =>
    request.post("/api/chat", {
      headers,
      data: {
        message: "Compare invoice extraction quality on the sample labelled dataset",
        dataset_id: datasetId,
        mode: "demo",
      },
    }),
  );
  expect(chatResponse.status(), await chatResponse.text()).toBe(422);
  expect(await listSessionIds()).toEqual(sessionsBefore);

  // 2. Create a real, empty session. POST /api/sessions takes no body and
  //    contacts no provider, so this allocates state without any inference.
  const createResponse = await guarded("session creation request failed", () =>
    request.post("/api/sessions", { headers }),
  );
  expect(createResponse.ok(), await createResponse.text()).toBeTruthy();
  const { session_id: sessionId } = await createResponse.json();
  expect(sessionId).toMatch(/^[a-f0-9]{12}$/);

  // 3. Explicit mode:"demo" on /run is a 422 and allocates no run. The spec is
  //    otherwise valid, so only `mode` can be responsible for the rejection.
  const spec = {
    benchmark_type: "extraction",
    category: "invoice",
    fields: ["invoice_number", "date", "vendor", "total"],
    candidates: [{ name: "tesseract", kind: "local_tool", use_fallback: true }],
    dataset: { dataset_id: datasetId },
  };
  const runResponse = await guarded("run rejection request failed", () =>
    request.post(`/api/sessions/${sessionId}/run`, {
      headers,
      data: { spec, mode: "demo" },
    }),
  );
  expect(runResponse.status(), await runResponse.text()).toBe(422);

  const sessionResponse = await guarded("session detail request failed", () =>
    request.get(`/api/sessions/${sessionId}`, { headers }),
  );
  expect(sessionResponse.ok(), await sessionResponse.text()).toBeTruthy();
  const session = await sessionResponse.json();
  expect(session.run_history).toEqual([]);
  expect(session.latest_run_id).toBeFalsy();
  expect(session.is_running).toBeFalsy();
  expect(session.results).toBeFalsy();

  // 4. The rejection body carries no provider or internal detail.
  const rejection = await runResponse.text();
  expect(rejection).not.toContain("demo");
  expect(rejection.toLowerCase()).not.toContain("traceback");
});
