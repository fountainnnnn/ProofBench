// Static guarantees about the live smoke test, checked without running it.
//
// The live smoke spec is the only test that authenticates with a real tenant
// token and spends real money. Both of its safety properties — bounded cost and
// no credential in on-disk artifacts — are structural, so they can be asserted
// by reading the source. This runs in CI on every change; the live test does not.
import { readFileSync } from "node:fs";
import { inspect } from "node:util";
import { describe, expect, it } from "vitest";
import { guarded } from "../e2e/liveSmokeRequest.js";

const spec = readFileSync(new URL("../e2e/live-smoke.spec.js", import.meta.url), "utf8");
const productionSpec = readFileSync(new URL("../e2e/production.spec.js", import.meta.url), "utf8");
const config = readFileSync(new URL("../playwright.config.js", import.meta.url), "utf8");

describe("production E2E credential secrecy", () => {
  it("disables browser artifacts globally because the production spec accepts a token", () => {
    const globalUse = config.slice(config.indexOf("use:"), config.indexOf("projects:"));
    expect(globalUse).toContain('trace: "off"');
    expect(globalUse).toContain('video: "off"');
    expect(globalUse).toContain('screenshot: "off"');
    expect(globalUse).not.toContain("retain-on-failure");
    expect(globalUse).not.toContain("only-on-failure");
  });

  it("omits a placeholder bearer and guards every APIRequest operation", () => {
    expect(productionSpec).toMatch(
      /authenticated \? \{ Authorization: `Bearer \$\{token\}` \} : \{\}/,
    );
    const calls = productionSpec.match(/request\.(get|post)\(/g) || [];
    const guardedCalls = productionSpec.match(
      /guarded\(\s*"[^"]+",\s*\(\) =>\s*\n?\s*request\.(get|post)\(/g,
    ) || [];
    expect(calls.length).toBeGreaterThan(0);
    expect(guardedCalls).toHaveLength(calls.length);
  });
});

describe("live smoke cost bound", () => {
  it("stops the session in a finally block when it never went terminal", () => {
    expect(spec).toContain("} finally {");
    expect(spec).toMatch(/if \(sessionId && !terminal\)/);
    expect(spec).toMatch(/\/stop`/);
  });

  it("marks the run terminal only after the poll observes a terminal status", () => {
    // `terminal` gates cleanup, so it must be set from the poll result rather
    // than optimistically at the top of the test.
    const terminalPoll = spec.indexOf("TERMINAL_STATUSES.includes(results.status)");
    const terminalAssignment = spec.indexOf("terminal = true");
    expect(terminalPoll).toBeGreaterThan(-1);
    expect(terminalAssignment).toBeGreaterThan(terminalPoll);
  });

  it("stops polling on any terminal status, then asserts completed separately", () => {
    // Polling for "completed" would spend the entire budget on a run that had
    // already failed, so the wait and the verdict must be two distinct steps.
    const declared = spec.match(/const TERMINAL_STATUSES = \[([^\]]*)\]/);
    expect(declared).toBeTruthy();
    for (const status of ["completed", "failed", "stopped"]) {
      expect(declared[1]).toContain(`"${status}"`);
    }
    // The verdict is checked after the wait ends, not as the wait's condition.
    const terminalAssignment = spec.indexOf("terminal = true");
    const completedAssertion = spec.indexOf('.toBe("completed")');
    expect(completedAssertion).toBeGreaterThan(terminalAssignment);
  });

  it("does not stop a run that already reached a terminal state", () => {
    // Cleanup is owed only when the run may still be spending; `terminal` is
    // set before any post-wait assertion can throw into the finally block.
    const terminalAssignment = spec.indexOf("terminal = true");
    const cleanupGuard = spec.indexOf("if (sessionId && !terminal)");
    expect(cleanupGuard).toBeGreaterThan(terminalAssignment);
  });

  it("bounds the cleanup request and never fails the test on it", () => {
    expect(spec).toMatch(/timeout: CLEANUP_MS/);
    expect(spec).toMatch(/failOnStatusCode: false/);
    // Bare `catch {` — a bound error is exactly what must not exist here.
    expect(spec).toMatch(/} catch \{/);
  });

  it("keeps at least 60s of timeout beyond the budget for cleanup", () => {
    const match = spec.match(/test\.setTimeout\(([^)]*)\)/);
    expect(match).toBeTruthy();
    const headroom = Function(
      "BUDGET_MS",
      "CLEANUP_MS",
      `return (${match[1]}) - BUDGET_MS;`,
    )(15 * 60 * 1000, 20_000);
    expect(headroom).toBeGreaterThanOrEqual(60_000);
  });
});

describe("live smoke artifact secrecy", () => {
  it("disables trace, video, and screenshots at the spec level", () => {
    expect(spec).toMatch(
      /test\.use\(\{\s*trace: "off",\s*video: "off",\s*screenshot: "off"\s*\}\)/,
    );
  });

  it("gives the live smoke its own project with artifacts off", () => {
    const project = config.slice(config.indexOf('name: "live-smoke"'));
    expect(project).toContain('trace: "off"');
    expect(project).toContain('video: "off"');
    expect(project).toContain('screenshot: "off"');
    // The baseline production project keeps its retain-on-failure artifacts,
    // so it must not also pick up the credentialed spec.
    expect(config).toMatch(/testIgnore: \/live-smoke\\.spec\\.js\//);
  });

  it("redacts anything response-derived before it reaches a message", () => {
    expect(spec).toMatch(/function safeDetail\(/);
    expect(spec).toMatch(/text\.split\(token\)\.join\("\[redacted\]"\)/);
    // Both body-derived values that reach an assertion message go through it:
    // blocked_by from provider readiness, and the run's terminal status.
    expect(spec).toMatch(/safeDetail\(\(readiness\.blocked_by \|\| \[\]\)\.join/);
    expect(spec).toMatch(/safeDetail\(results\.status\)/);
  });

  it("interpolates no unredacted response value into an assertion message", () => {
    // Every `${...}` inside a double-quoted-or-backtick assertion message must
    // be a constant or a safeDetail() call. A raw body value would be a leak.
    const interpolations = spec.match(/\$\{[^}]*\}/g) || [];
    // Guard against a regex that silently stops matching: the loop below is
    // vacuous on an empty list.
    expect(interpolations.length).toBeGreaterThan(5);
    for (const interpolation of interpolations) {
      const safe =
        interpolation.includes("safeDetail(") ||
        /^\$\{(token|runId|sessionId|BUDGET_MS|label|encodeURIComponent\(sessionId\)|stopResponse\.status\(\))\}$/.test(
          interpolation,
        );
      expect(safe, `unredacted interpolation: ${interpolation}`).toBe(true);
    }
  });

  it("omits the Authorization header entirely when no token is configured", () => {
    // The local tokenless profile must send no header at all. A placeholder
    // ("Bearer ", "Bearer undefined") would be a credential-shaped value in the
    // call log of every request, which is exactly what the firewall exists to
    // prevent — and the server would reject it in authenticated mode.
    expect(spec).toMatch(
      /const AUTH_HEADERS = token \? \{ Authorization: `Bearer \$\{token\}` \} : \{\};/,
    );
    // No unconditional header object may survive alongside it.
    const headerLiterals = spec.match(/\{ ?Authorization:/g) || [];
    expect(headerLiterals).toHaveLength(1);
  });

  it("runs on the opt-in flag alone, without requiring a token", () => {
    // Gating `enabled` on the token would make the spec silently unrunnable
    // against the default local Compose profile.
    expect(spec).toMatch(/const enabled = process\.env\.PROOFBENCH_RUN_LIVE_SMOKE === "1";/);
    expect(spec).not.toMatch(/enabled = .*token\.length/);
  });

  it("keeps redaction correct when the token is absent", () => {
    // safeDetail must not split on an empty string: "".split("") explodes a
    // response body into characters and rejoins it with [redacted] between
    // every one, which would make failures unreadable. The ternary guards it.
    expect(spec).toMatch(/const redacted = token \? text\.split\(token\)\.join\("\[redacted\]"\) : text;/);
  });

  it("never interpolates the token or headers into a message", () => {
    // The token may appear exactly once, building the Authorization header.
    const uses = spec.match(/\$\{token\}/g) || [];
    expect(uses).toHaveLength(1);
    expect(spec).toContain("Authorization: `Bearer ${token}`");
    expect(spec).not.toMatch(/\$\{JSON\.stringify\(headers\)\}/);
    expect(spec).not.toMatch(/\$\{await .*\.text\(\)\}/);
    expect(spec).not.toMatch(/\$\{.*\.headers\(\)\}/);
  });
});

describe("live smoke transport-error containment", () => {
  it("routes every authenticated request through the guard", () => {
    // A bare `request.get(`/`request.post(` is an unguarded call whose transport
    // error would carry the Authorization header in Playwright's call log. The
    // only permitted occurrences are the ones inside a guarded() thunk.
    const calls = spec.match(/request\.(get|post)\(/g) || [];
    const guardedCalls = spec.match(/guarded\(\s*"[^"]+",\s*\(\) =>\s*\n?\s*request\.(get|post)\(/g) || [];
    expect(calls.length).toBeGreaterThan(0);
    expect(guardedCalls).toHaveLength(calls.length);
  });

  it("binds no error anywhere in the spec", () => {
    // Neither `catch (e)` nor any reference to a caught error may exist; the
    // whole point is that the original object is unreachable.
    expect(spec).not.toMatch(/catch\s*\(/);
    expect(spec).not.toMatch(/\berror\?\./);
    expect(spec).not.toMatch(/\bcause\b/);
  });

  it("labels the guard with fixed strings only", () => {
    // Every label is a plain double-quoted literal: no template interpolation,
    // so nothing from the request or the failure can be spliced into a message.
    const labels = spec.match(/guarded\(\s*([^,]+),/g) || [];
    expect(labels.length).toBeGreaterThan(0);
    for (const label of labels) {
      expect(label).toMatch(/guarded\(\s*"[^"$`]+",/);
    }
  });
});

describe("guarded", () => {
  // A stand-in for the shape of a Playwright transport error: the bearer token
  // shows up in the call-log text that Playwright appends to `message`.
  const SENTINEL = "sb_live_tok_SENTINEL_9f3c2a";
  const transportFailure = () => {
    const error = new Error(
      [
        "apiRequestContext.get: connect ECONNREFUSED 127.0.0.1:8080",
        "Call log:",
        "  - → GET http://127.0.0.1:8080/api/providers",
        `  - user-agent: node`,
        `  - Authorization: Bearer ${SENTINEL}`,
      ].join("\n"),
    );
    error.name = "APIRequestError";
    return error;
  };

  const leaks = (value) => {
    const text = String(value ?? "");
    return text.includes(SENTINEL) || text.includes("Authorization");
  };

  it("returns the operation's value when it succeeds", async () => {
    await expect(guarded("nope", async () => ({ ok: 1 }))).resolves.toEqual({ ok: 1 });
  });

  it("replaces a transport error with the fixed label", async () => {
    // Sanity check that the fake really does carry the secret, so a passing
    // assertion below means containment rather than a toothless fixture.
    expect(leaks(transportFailure().message)).toBe(true);

    await expect(
      guarded("provider readiness request failed", async () => {
        throw transportFailure();
      }),
    ).rejects.toThrow("provider readiness request failed");
  });

  it("leaks the sentinel through no property of the thrown error", async () => {
    let thrown;
    try {
      await guarded("provider readiness request failed", async () => {
        throw transportFailure();
      });
    } catch (caught) {
      thrown = caught;
    }

    expect(thrown).toBeInstanceOf(Error);
    expect(thrown.message).toBe("provider readiness request failed");
    // The ES2022 error chain must be empty; a `cause` would serialize the
    // discarded call log straight back into reporter output.
    expect(thrown.cause).toBeUndefined();
    expect("cause" in thrown).toBe(false);
    expect(Object.keys(thrown)).toHaveLength(0);

    // Every rendering a test reporter might reach for.
    for (const rendering of [
      thrown.message,
      thrown.stack,
      String(thrown),
      thrown.toString(),
      JSON.stringify(thrown),
      JSON.stringify(thrown, Object.getOwnPropertyNames(thrown)),
      inspect(thrown, { depth: null }),
    ]) {
      expect(leaks(rendering)).toBe(false);
    }
  });

  it("discards non-Error throws that embed the sentinel", async () => {
    // A rejected string or a plain object must be dropped just as completely.
    for (const payload of [
      `Authorization: Bearer ${SENTINEL}`,
      { headers: { Authorization: `Bearer ${SENTINEL}` } },
    ]) {
      let thrown;
      try {
        await guarded("cleanup stop request failed", async () => {
          throw payload;
        });
      } catch (caught) {
        thrown = caught;
      }
      expect(thrown).toBeInstanceOf(Error);
      expect(thrown.message).toBe("cleanup stop request failed");
      expect(leaks(inspect(thrown, { depth: null }))).toBe(false);
      expect(leaks(thrown.stack)).toBe(false);
    }
  });

  it("contains a synchronous throw as well as a rejection", async () => {
    await expect(
      guarded("dataset request failed", () => {
        throw transportFailure();
      }),
    ).rejects.toThrow("dataset request failed");
  });
});

describe("safeDetail", () => {
  // Reimplemented from the spec source so the redaction rule itself is tested
  // rather than only its presence.
  const safeDetail = (value, token) => {
    const text = String(value ?? "");
    const redacted = token ? text.split(token).join("[redacted]") : text;
    return redacted.replace(/[^\x20-\x7e]/g, " ").slice(0, 200);
  };

  it("removes every occurrence of the token", () => {
    const out = safeDetail("a tok-123 b tok-123", "tok-123");
    expect(out).toBe("a [redacted] b [redacted]");
    expect(out).not.toContain("tok-123");
  });

  it("caps length and strips control characters", () => {
    expect(safeDetail("x".repeat(500), "")).toHaveLength(200);
    expect(safeDetail("a\nb c", "")).toBe("a b c");
  });
});
