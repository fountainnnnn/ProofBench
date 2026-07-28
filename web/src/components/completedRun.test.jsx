// @vitest-environment jsdom
//
// The completed-run view is read to make a decision. The ranking leads, the
// evidence labels stay compact, and the conversation and execution trace start
// folded away so a scraped page cannot bury the answer.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import ChatThread from "./ChatThread.jsx";
import AgentTraceCard from "./AgentTraceCard.jsx";
import ResultsCard from "./ResultsCard.jsx";

vi.mock("../api.js", () => ({ prepareReportPdf: vi.fn() }));

const measured = { status: "measured", mode: "real", datasetKind: "synthetic" };

const metrics = {
  tesseract: { exact_accuracy: 0.93, field_f1: 0.95, cer: 0.04, n_docs: 15 },
  easyocr: { exact_accuracy: 0.81, field_f1: 0.86, cer: 0.09, n_docs: 15 },
};

function renderCompleted(overrides = {}) {
  return render(
    <ChatThread
      messages={[{ role: "user", text: "compare tesseract and easyocr" }, { role: "assistant", text: "Comparing both." }]}
      trace={[{ tool: "scrape_docs", status: "ok", detail: "<p>Tesseract install guide</p>" }]}
      sandboxLogs={{ "sandbox-1": [{ phase: "done", line: "finished" }] }}
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

describe("completed run is decision first", () => {
  it("places the ranking and recommendation before the conversation and the trace", () => {
    const { container } = renderCompleted();
    const text = container.textContent;
    const recommendation = text.indexOf("Recommendation");
    const conversation = text.indexOf("Conversation");
    /* A settled run collapses its trace to one activity line ("Searched the
       web · …"), so the ordering is asserted against that line rather than the
       old "Execution trace" card heading. */
    const activity = text.indexOf("Read documentation");

    expect(recommendation).toBeGreaterThanOrEqual(0);
    expect(conversation).toBeGreaterThan(recommendation);
    expect(activity).toBeGreaterThan(conversation);
  });

  it("names the winning candidate and its margin over the runner up", () => {
    renderCompleted();
    expect(screen.getAllByText("tesseract").length).toBeGreaterThan(0);
    expect(screen.getByText(/12\.0 points ahead of easyocr/)).toBeTruthy();
    expect(screen.getByText(/Ranked first of 2/)).toBeTruthy();
  });

  it("folds the conversation away by default and opens it on request", () => {
    renderCompleted();
    const toggle = screen.getByRole("button", { name: /Conversation/ });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByText("compare tesseract and easyocr")).toBeNull();

    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("compare tesseract and easyocr")).toBeTruthy();
  });

  it("collapses a settled trace to one activity line and opens the sources on request", () => {
    renderCompleted({
      trace: [{
        tool: "scrape_docs",
        status: "ok",
        detail: '[{"title": "Tesseract install guide", "url": "https://tesseract-ocr.github.io/install"}]',
      }],
    });
    /* The detail is not in the thread at all — it lives in a side panel — and
       the panel lists the PAGES consulted rather than the raw call payload. */
    const line = screen.getByRole("button", { name: /Searched 1 site/ });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryByText("Tesseract install guide")).toBeNull();

    fireEvent.click(line);
    const panel = screen.getByRole("dialog", { name: /Sources/ });
    expect(panel).toBeTruthy();
    const link = within(panel).getByRole("link", { name: /Tesseract install guide/ });
    expect(link.getAttribute("href")).toBe("https://tesseract-ocr.github.io/install");
    // The raw payload must not leak into the panel.
    expect(panel.textContent).not.toContain('"url"');
  });

  it("leads with the decision even when no terminal phase event was replayed", () => {
    renderCompleted({ phaseState: null });
    const text = document.body.textContent;
    expect(text.indexOf("Recommendation")).toBeLessThan(text.indexOf("Conversation"));
  });

  it("keeps a live turn in conversation order while the agent is still replying", () => {
    renderCompleted({ phaseState: null, typing: true });
    expect(screen.queryByRole("button", { name: /Conversation/ })).toBeNull();
    expect(screen.getByText("compare tesseract and easyocr")).toBeTruthy();
  });

  it("streams a live run's work inline, after the message that prompted it", () => {
    renderCompleted({ phaseState: { phase: "RUNNING" }, running: true, results: null, report: null });
    const text = document.body.textContent;
    /* Live work is bare text in the thread, not a card to expand: the reader
       sees what the agent is doing without having to open anything. This
       fixture's one call has already returned, so the line reads in the past
       tense — the gerund ("Reading…") is reserved for calls still in flight. */
    expect(text.indexOf("compare tesseract and easyocr")).toBeLessThan(text.indexOf("Read documentation"));
    expect(screen.queryByRole("button", { name: /Execution trace/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Conversation/ })).toBeNull();
  });

  it("says a tool is still working while its call is in flight", () => {
    renderCompleted({
      phaseState: { phase: "RUNNING" },
      running: true,
      results: null,
      report: null,
      trace: [{ tool: "web_search", status: "start", args_summary: "query=ocr" }],
    });
    expect(document.body.textContent).toContain("Searching the web");
  });

  it("still blocks a run whose conclusive provenance disagrees", () => {
    renderCompleted({ specProvenance: { status: "synthetic", mode: "demo", datasetKind: "synthetic" } });
    expect(screen.getByText(/Results provenance does not match the benchmark specification/)).toBeTruthy();
    expect(document.body.textContent).not.toContain("93.0%");
  });

  it("renders a measured run restored beside an unmarked specification", () => {
    // The regression: a restored spec carries no marker, which must not be read
    // as a contradiction of an authoritative measured result.
    renderCompleted({ specProvenance: { status: "pending", mode: null, datasetKind: "synthetic" } });
    expect(screen.queryByText(/Results provenance does not match/)).toBeNull();
    expect(screen.getByText("Recommendation")).toBeTruthy();
    expect(screen.getByText(/Report body text/)).toBeTruthy();
  });
});

describe("execution basis is taken from the backend", () => {
  it("renders a declared execution mode rather than inventing one", () => {
    render(<ResultsCard metrics={metrics} phase="DONE" executionMode="verified_in_daytona" />);
    expect(screen.getByText("Verified by execution")).toBeTruthy();
  });

  it("reads a documentation-only assessment from the candidate rows", () => {
    render(
      <ResultsCard
        metrics={{ acme: { rating: 70, daytona_triggered: false }, beta: { rating: 40, daytona_triggered: false } }}
        phase="DONE"
      />
    );
    expect(screen.getByText("Compared from documentation")).toBeTruthy();
  });

  it("does not claim a partly verified comparison was fully verified", () => {
    render(
      <ResultsCard
        metrics={{ acme: { rating: 70, daytona_triggered: true }, beta: { rating: 40, daytona_triggered: false } }}
        phase="DONE"
      />
    );
    expect(screen.getByText("Partly verified by execution")).toBeTruthy();
  });

  it("claims nothing when the backend reported no basis", () => {
    render(<ResultsCard metrics={metrics} phase="DONE" />);
    expect(screen.queryByText(/Verified by execution/)).toBeNull();
    expect(screen.queryByText(/Compared from documentation/)).toBeNull();
    expect(screen.getByText("Recommendation")).toBeTruthy();
  });
});

describe("failed benchmark copy", () => {
  it("explains that setup failed before a run existed and gives a retry path", () => {
    render(<ResultsCard metrics={null} phase="FAILED" />);
    expect(screen.getByText(/Benchmark setup failed before a run was started/)).toBeTruthy();
    expect(screen.getByText(/then retry/)).toBeTruthy();
  });

  it("keeps the run failure wording when a run id exists", () => {
    render(<ResultsCard metrics={null} phase="FAILED" runId="run-1" />);
    expect(screen.getByText("This run failed before usable metrics were produced.")).toBeTruthy();
  });
});

describe("raw trace detail is capped", () => {
  const flood = `<div>${"scraped documentation body ".repeat(400)}</div>`;

  it("shows a bounded preview of a scraped page and expands only on request", () => {
    render(
      <AgentTraceCard
        trace={[{ tool: "scrape_docs", status: "ok", detail: flood }]}
        sandboxLogs={{}}
        phaseState={{ phase: "DONE" }}
      />
    );
    const group = screen.getByRole("button", { name: /scrape_docs/ });
    const initial = document.body.textContent.length;
    expect(initial).toBeLessThan(2000);
    expect(document.body.textContent).not.toContain("<div>");

    const showMore = screen.getByRole("button", { name: /Show more/ });
    fireEvent.click(showMore);
    const expanded = document.body.textContent.length;
    expect(expanded).toBeGreaterThan(initial);
    // Even the deliberate expansion stays bounded.
    expect(expanded).toBeLessThan(4000);
    expect(within(group).queryByText("<div>")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Show less" }));
    expect(document.body.textContent.length).toBe(initial);
  });

  it("summarizes repeated tool calls instead of listing every one", () => {
    render(
      <AgentTraceCard
        trace={Array.from({ length: 30 }, (_, i) => ({ tool: "scrape_docs", status: "ok", detail: `page ${i}` }))}
        sandboxLogs={{}}
        phaseState={{ phase: "DONE" }}
      />
    );
    expect(screen.getByText("30 tool calls across 1 tool")).toBeTruthy();
    expect(screen.getByText(/22 earlier calls not shown/)).toBeTruthy();
    expect(screen.queryByText("page 0")).toBeNull();
    expect(screen.getByText("page 29")).toBeTruthy();
  });
});
