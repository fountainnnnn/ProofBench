// @vitest-environment jsdom
//
// The Runs table must never present a session as measured evidence on the
// strength of its `mode`. Every session row carries mode:"real" — it is set at
// creation and survives an empty, running, failed, or unverified session — so
// the badge is driven only by the backend's provenance marker.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

const api = vi.hoisted(() => ({ listSessions: vi.fn() }));
vi.mock("../api.js", () => api);

import Runs from "./Runs.jsx";

afterEach(cleanup);
beforeEach(() => vi.clearAllMocks());

function session(overrides) {
  return {
    id: "s1",
    title: "Invoice benchmark",
    phase: "DONE",
    // Present and "real" on every row on purpose: these tests exist to prove it
    // is not what decides the badge.
    mode: "real",
    is_running: false,
    created_at: "2026-07-20T00:00:00Z",
    ...overrides,
  };
}

async function renderRows(rows) {
  api.listSessions.mockResolvedValue(rows);
  render(<MemoryRouter><Runs /></MemoryRouter>);
  await screen.findByRole("table");
}

describe("Runs evidence badge", () => {
  it("renders Measured only for a run with persisted measured provenance", async () => {
    await renderRows([session({ provenance: "measured", latest_run_id: "abc123abc123" })]);
    expect(await screen.findByText("Measured")).toBeTruthy();
  });

  it("does not call an empty session measured despite mode real", async () => {
    await renderRows([session({ phase: "INTAKE", provenance: "pending" })]);
    expect(await screen.findByText("Pending")).toBeTruthy();
    expect(screen.queryByText("Measured")).toBeNull();
  });

  it("does not call a running session measured", async () => {
    await renderRows([
      session({ phase: "RUNNING", is_running: true, provenance: "pending" }),
    ]);
    expect(await screen.findByText("Real execution")).toBeTruthy();
    expect(screen.queryByText("Measured")).toBeNull();
  });

  it("renders the running badge on the accessible accent pair, not the --info tint", async () => {
    // --info text on a 10% --info tint measures 3.5:1 and failed axe's serious
    // color-contrast rule in production. Pin the replacement so it stays fixed.
    await renderRows([
      session({ phase: "RUNNING", is_running: true, provenance: "pending" }),
    ]);
    const badge = await screen.findByText("Real execution");
    expect(badge.className).toContain("text-[color:var(--accent)]");
    expect(badge.className).toContain("bg-[color:var(--accent-soft)]");
    expect(badge.className).not.toContain("var(--info)");
  });

  it("reports a failed run as Failed, not measured", async () => {
    await renderRows([
      session({
        phase: "FAILED",
        provenance: "pending",
        latest_run_id: "abc123abc123",
        latest_run_failed: true,
      }),
    ]);
    // The evidence badge is a span; the phase filter chip is a same-text button,
    // so scope the query to the badge.
    expect(await screen.findByText("Failed", { selector: "span" })).toBeTruthy();
    expect(screen.queryByText("Measured")).toBeNull();
  });

  it("renders error badges on the accessible error pair, not the --err tint", async () => {
    // --err text on a 10% --err tint measures 4.4:1 — just under the 4.5:1 axe
    // requires at 11px, and it failed the rule in production for both the
    // FAILED phase badge and the Failed/Unverified evidence badges. Pin the
    // replacement pair (--err-strong on --err-soft, 7:1) so it stays fixed.
    await renderRows([
      session({
        phase: "FAILED",
        provenance: "pending",
        latest_run_id: "abc123abc123",
        latest_run_failed: true,
      }),
      session({ id: "s2", provenance: "unverified", latest_run_id: "def456def456" }),
    ]);
    // The evidence badges are the only coloured chips now — the phase column is
    // plain text, so only these two carry the error pair.
    const badges = [
      await screen.findByText("Failed", { selector: "span" }),
      await screen.findByText("Unverified"),
    ];
    for (const badge of badges) {
      expect(badge.className).toContain("text-[color:var(--err-strong)]");
      expect(badge.className).toContain("bg-[color:var(--err-soft)]");
      // The old translucent tint must not come back under any spelling.
      expect(badge.className).not.toContain("var(--err)_10%");
      expect(badge.className).not.toContain("text-[color:var(--err)]");
    }
  });

  it("reports metrics without a trustworthy marker as Unverified", async () => {
    await renderRows([
      session({ provenance: "unverified", latest_run_id: "abc123abc123" }),
    ]);
    expect(await screen.findByText("Unverified")).toBeTruthy();
    expect(screen.queryByText("Measured")).toBeNull();
  });

  it("keeps a legacy demo run labelled historical synthetic", async () => {
    await renderRows([
      session({ mode: "demo", provenance: "synthetic", latest_run_id: "abc123abc123" }),
    ]);
    expect(await screen.findByText("Historical synthetic")).toBeTruthy();
    expect(screen.queryByText("Measured")).toBeNull();
  });

  it("falls back to Pending when the API omits provenance entirely", async () => {
    // An older API build, or a field that failed to serialize. Absence of
    // evidence must not read as evidence.
    await renderRows([session({ phase: "DONE", latest_run_id: "abc123abc123" })]);
    expect(await screen.findByText("Pending")).toBeTruthy();
    expect(screen.queryByText("Measured")).toBeNull();
  });
});
