// @vitest-environment jsdom
//
// The orchestrator proposes a benchmark as a fenced JSON block. Printed raw it
// arrived in the thread as a 40-line wall of braces the reader had to parse by
// eye. These pin the two behaviours that fixed it: a spec block renders as a
// summary, and anything that is not a spec still renders as ordinary code.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import ChatThread from "./ChatThread.jsx";

vi.mock("../api.js", () => ({ prepareReportPdf: vi.fn() }));

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(cleanup);

const SPEC = {
  benchmark_type: "extraction",
  category: "invoice OCR",
  fields: ["invoice_number", "total"],
  candidates: [
    { name: "nanonets", docs_url: "https://nanonets.com/docs/", kind: "hosted_api" },
    { name: "tesseract", kind: "local_tool", role: "build_component" },
  ],
};

function renderThread(text, overrides = {}) {
  return render(
    <ChatThread
      messages={[{ role: "assistant", text }]}
      trace={[]}
      sandboxLogs={{}}
      phaseState={null}
      spec={null}
      results={null}
      report={null}
      runId={null}
      onRun={vi.fn()}
      onStop={vi.fn()}
      running={false}
      stopping={false}
      {...overrides}
    />,
  );
}

describe("a proposed specification renders as a summary, not raw JSON", () => {
  it("names the benchmark, its fields, and its candidates", () => {
    const { container } = renderThread(
      "Here is the proposal:\n\n```json\n" + JSON.stringify(SPEC, null, 2) + "\n```",
    );

    expect(screen.getByText("Proposed benchmark")).toBeTruthy();
    expect(screen.getByText("extraction")).toBeTruthy();
    expect(screen.getByText("invoice OCR")).toBeTruthy();
    expect(screen.getByText("invoice_number")).toBeTruthy();
    expect(screen.getByText("2 candidates")).toBeTruthy();
    expect(screen.getByText("nanonets")).toBeTruthy();
    expect(screen.getByText("Hosted API")).toBeTruthy();
    // A harness part is labelled so it is not read as a tool being judged.
    expect(screen.getByText("build component")).toBeTruthy();
    // The braces themselves are gone: that was the whole complaint.
    expect(container.textContent).not.toContain('"benchmark_type"');
    expect(container.querySelector("code")).toBeNull();
  });

  it("links a candidate's documentation only when the URL is safe", () => {
    renderThread("```json\n" + JSON.stringify(SPEC) + "\n```");
    const link = screen.getByRole("link", { name: "docs" });
    expect(link.getAttribute("href")).toBe("https://nanonets.com/docs/");
    expect(link.getAttribute("rel")).toContain("noopener");
  });

  it("leaves JSON that is not a specification as a normal code block", () => {
    const { container } = renderThread('```json\n{"unrelated": true}\n```');
    expect(screen.queryByText("Proposed benchmark")).toBeNull();
    expect(container.querySelector("code")).toBeTruthy();
  });

  it("leaves malformed JSON alone rather than swallowing it", () => {
    const { container } = renderThread("```json\n{not valid\n```");
    expect(screen.queryByText("Proposed benchmark")).toBeNull();
    expect(container.textContent).toContain("not valid");
  });
});

describe("the results placeholder waits until results are what comes next", () => {
  const withPhase = (phase, extra = {}) =>
    renderThread("working on it", { phaseState: { phase }, running: true, ...extra });

  it("stays hidden while the run is still deciding what to benchmark", () => {
    for (const phase of ["INTAKE", "SPEC_CONFIRM"]) {
      const { container, unmount } = withPhase(phase);
      // The next card to appear is the specification, so promising results
      // there shows a loading state for something that is not coming.
      expect(container.textContent).not.toMatch(/exact acc/i);
      expect(container.querySelector('[data-testid="results-card"]')).toBeNull();
      unmount();
    }
  });

  it("appears once the run has moved past the specification", () => {
    const { container } = withPhase("RUNNING");
    expect(container.textContent.length).toBeGreaterThan(0);
    // Something rendered for the running run rather than nothing at all.
    expect(container.querySelector(".pb-skeleton, [class*='rounded']")).toBeTruthy();
  });
});
