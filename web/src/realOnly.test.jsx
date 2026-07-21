// @vitest-environment jsdom
//
// ProofBench writes real runs only. These tests pin the user-visible half of
// that guarantee: there is no mode control, every new write asks for "real",
// unproven metrics are withheld, and runs kept from an earlier demo-capable
// version stay readable but are always labelled as history.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

afterEach(cleanup);

describe("no demo/real mode control exists in the UI", () => {
  it("exposes exactly one run mode as a module constant, not as caller state", async () => {
    const api = await import("./api.js");
    expect(api.RUN_MODE).toBe("real");
    // postChat/startRun take no mode argument, so no UI path can vary it.
    expect(api.postChat.length).toBe(3);
    expect(api.startRun.length).toBe(2);
  });

  it("ships no demo/real toggle, switch, or selector in any source file", async () => {
    const modules = import.meta.glob("./{components,pages}/*.jsx", {
      eager: true,
      query: "?raw",
      import: "default",
    });
    const files = Object.entries(modules).filter(([path]) => !path.includes(".test."));
    expect(files.length).toBeGreaterThan(0);

    for (const [path, source] of files) {
      // A control that lets a person pick a mode would have to render an
      // interactive element named for one. Prose about historical runs is fine.
      expect(source, `${path} must not offer a demo mode control`)
        .not.toMatch(/(role=|type=)["'](switch|radio|checkbox)["'][^>]*demo/i);
      expect(source, `${path} must not offer a mode selector`)
        .not.toMatch(/<(select|input)[^>]*\bname=["']mode["']/i);
      expect(source, `${path} must not set mode from component state`)
        .not.toMatch(/setMode\s*\(/);
    }
  });

  it("renders no demo-mode control on the benchmark composer", async () => {
    const { default: Composer } = await import("./components/Composer.jsx");
    render(<Composer onSend={vi.fn()} onUpload={vi.fn()} dataset={null} />);

    expect(screen.queryByRole("switch")).toBeNull();
    expect(screen.queryByRole("radio", { name: /demo|real/i })).toBeNull();
    expect(screen.queryByLabelText(/demo mode/i)).toBeNull();
    expect(screen.queryByRole("button", { name: /^demo$/i })).toBeNull();
  });
});

describe("every new write requests a real run", () => {
  beforeEach(async () => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    // The tokenless local profile holds no credential, so there is no session
    // state to clear between tests; a clean fetch stub is the whole reset.
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 200 })));
  });

  it("sends mode:'real' on chat and run, and cannot be talked out of it", async () => {
    // A fresh Response per call: a body can only be read once.
    const fetchMock = vi.fn().mockImplementation(async () => new Response(
      JSON.stringify({ session_id: "session-1", run_id: "run-1" }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    ));
    vi.stubGlobal("fetch", fetchMock);
    const { postChat, startRun } = await import("./api.js");

    // Extra positional arguments are the closest a caller can get to asking
    // for another mode. They must be ignored.
    await postChat("compare two extractors", "session-1", "dataset-1", "demo");
    await startRun("session-1", { candidates: [] }, "demo");

    for (const call of fetchMock.mock.calls) {
      const body = JSON.parse(call[1].body);
      expect(body.mode).toBe("real");
    }
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("never sends a demo marker on a dataset write", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ dataset_id: "dataset-1" }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    ));
    vi.stubGlobal("fetch", fetchMock);
    const { uploadDataset } = await import("./api.js");

    await uploadDataset({ useSynthetic: true });

    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    // The sample labelled dataset is a real, labelled input — not a demo mode.
    expect(body).toEqual({ use_synthetic: true });
    expect(body.mode).toBeUndefined();
    expect(body.demo_mode).toBeUndefined();
  });
});

describe("evidence gating in the benchmark thread", () => {
  let ChatThread;

  beforeEach(async () => {
    vi.doMock("./api.js", () => ({ prepareReportPdf: vi.fn() }));
    Element.prototype.scrollIntoView = vi.fn();
    ({ default: ChatThread } = await import("./components/ChatThread.jsx"));
  });

  function renderThread(resultsProvenance) {
    return render(
      <ChatThread
        messages={[]}
        trace={[]}
        sandboxLogs={{}}
        phaseState={{ phase: "DONE" }}
        results={{ alpha: { exact_accuracy: 0.93 } }}
        report={{ markdown: "report body text", provenance: resultsProvenance }}
        specProvenance={resultsProvenance}
        resultsProvenance={resultsProvenance}
        onRun={vi.fn()}
        onStop={vi.fn()}
        running={false}
        stopping={false}
      />
    );
  }

  it("withholds metrics and the report while evidence is pending", () => {
    renderThread({ status: "pending", mode: null, datasetKind: "upload" });

    expect(screen.getByText(/Awaiting verified results/)).toBeTruthy();
    expect(screen.getByText(/Metrics stay hidden until this run has immutable/)).toBeTruthy();
    expect(document.body.textContent).not.toContain("0.93");
    expect(screen.queryByText("report body text")).toBeNull();
  });

  it("withholds metrics for unverified evidence", () => {
    renderThread({ status: "unverified", mode: null, datasetKind: "upload" });

    expect(screen.getByText(/Unverified\. Results withheld/)).toBeTruthy();
    expect(document.body.textContent).not.toContain("0.93");
    expect(screen.queryByText("report body text")).toBeNull();
  });

  it("shows measured metrics with no provenance caveat", () => {
    renderThread({ status: "measured", mode: "real", datasetKind: "upload" });

    expect(screen.queryByText(/Metrics stay hidden/)).toBeNull();
    expect(screen.queryByText(/Historical synthetic/)).toBeNull();
    expect(screen.getByText("Results")).toBeTruthy();
  });

  it("keeps a historical synthetic run readable but visibly labelled", () => {
    renderThread({ status: "synthetic", mode: "demo", datasetKind: "synthetic" });

    // Readable: the card renders rather than withholding.
    expect(screen.queryByText(/Metrics stay hidden/)).toBeNull();
    // Labelled: it can never be mistaken for a measured result.
    expect(screen.getByText("Historical synthetic results")).toBeTruthy();
  });
});
