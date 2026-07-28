// @vitest-environment jsdom
//
// A finished benchmark ends the run, not the conversation. When the user keeps
// talking, their message and the reply streaming back have to be visible, and a
// candidate that never ran must never read as one that scored zero.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import ChatThread from "./ChatThread.jsx";
import ResultsCard from "./ResultsCard.jsx";

vi.mock("../api.js", () => ({ prepareReportPdf: vi.fn() }));

const measured = { status: "measured", mode: "real", datasetKind: "synthetic" };

const metrics = {
  tesseract: { exact_accuracy: 0.93, field_f1: 0.95, cer: 0.04, n_docs: 15, status: "ok" },
  easyocr: { exact_accuracy: 0.81, field_f1: 0.86, cer: 0.09, n_docs: 15, status: "ok" },
};

function renderThread(overrides = {}) {
  return render(
    <ChatThread
      messages={[
        { role: "user", text: "compare tesseract and easyocr" },
        { role: "assistant", text: "Comparing both." },
        { role: "user", text: "why did easyocr lose on dates" },
      ]}
      trace={[]}
      sandboxLogs={{}}
      phaseState={{ phase: "DONE" }}
      spec={{ candidates: [{ name: "tesseract" }], fields: ["total"] }}
      results={metrics}
      report={{ markdown: "Report body text", provenance: measured }}
      specProvenance={null}
      resultsProvenance={measured}
      runId="run-1"
      onRun={vi.fn()}
      onStop={vi.fn()}
      running={false}
      stopping={false}
      {...overrides}
    />
  );
}

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(cleanup);

describe("continuing the conversation after a run", () => {
  it("folds the conversation away while the user is only reading the decision", () => {
    const { container } = renderThread();
    expect(container.textContent).toContain("Conversation");
    // Collapsed: the follow-up question is not on screen.
    expect(screen.queryByText("why did easyocr lose on dates")).toBeNull();
  });

  it("keeps the follow-up and its reply on screen once the user carries on", () => {
    renderThread({ conversationLive: true });
    expect(screen.getByText("why did easyocr lose on dates")).toBeTruthy();
    expect(screen.getByText("Comparing both.")).toBeTruthy();
  });

  it("still shows the ranking underneath, so the answer stays reachable", () => {
    const { container } = renderThread({ conversationLive: true });
    const text = container.textContent;
    expect(text.indexOf("why did easyocr lose on dates"))
      .toBeLessThan(text.indexOf("Recommendation"));
  });

  it("does not fold the thread away while a reply is streaming in", () => {
    renderThread({ typing: true });
    expect(screen.getByText("why did easyocr lose on dates")).toBeTruthy();
  });
});

describe("a candidate that never ran", () => {
  const withFailure = {
    tesseract: { exact_accuracy: 0.93, field_f1: 0.95, cer: 0.04, n_docs: 15, status: "ok" },
    openai_vision: {
      exact_accuracy: null,
      field_f1: null,
      cer: null,
      mean_latency_s: null,
      failure_rate: 1,
      cost_per_1k_docs: null,
      setup_complexity: 1,
      n_docs: 15,
      documents_scored: 0,
      status: "no_result",
      error_summary: "RateLimitError: Error code: 429 - quota exceeded",
    },
  };

  it("names the reason instead of presenting a zero score", () => {
    render(<ResultsCard metrics={withFailure} report={null} runId="run-1" phase="DONE" running={false} />);
    const entry = screen.getByText("openai_vision").closest("li");
    expect(within(entry).getByText(/Did not run: RateLimitError/)).toBeTruthy();
    expect(within(entry).queryByText("0.0%")).toBeNull();
  });

  it("keeps it out of the comparison table so the ranking is all measured rows", () => {
    const { container } = render(
      <ResultsCard metrics={withFailure} report={null} runId="run-1" phase="DONE" running={false} />
    );
    const bodyRows = [...container.querySelectorAll("tbody tr")];
    expect(bodyRows).toHaveLength(1);
    expect(bodyRows[0].textContent).toContain("tesseract");
    expect(screen.getByText("Not compared · 1")).toBeTruthy();
    expect(screen.getByText(/Ranked first of 1/)).toBeTruthy();
  });
});
