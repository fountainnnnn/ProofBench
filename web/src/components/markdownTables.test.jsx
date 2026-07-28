// @vitest-environment jsdom
//
// The agent answers comparison questions in markdown tables. Without the GFM
// plugin react-markdown renders those as a paragraph of pipe characters, which
// is exactly how a "top 5 services" reply arrived in the thread — unreadable,
// while the same table in the generated report rendered fine.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import ChatThread from "./ChatThread.jsx";

vi.mock("../api.js", () => ({ prepareReportPdf: vi.fn() }));

const TABLE = [
  "| Service | What it is |",
  "|---|---|",
  "| AWS Bedrock | Fully managed RAG |",
  "| Pinecone | Vector DB as a service |",
].join("\n");

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(cleanup);

function renderThread(text, overrides = {}) {
  return render(
    <ChatThread
      messages={[{ role: "assistant", text }]}
      trace={[]}
      {...overrides}
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
    />,
  );
}

describe("agent activity is tied to its own turn", () => {
  const twoTurns = {
    messages: [
      { role: "user", text: "first question" },
      { role: "assistant", text: "first answer" },
      { role: "user", text: "second question" },
      { role: "assistant", text: "second answer" },
    ],
    // Stamped by Benchmark.jsx as each event arrives: turn 1's work produced
    // the first answer, turn 3's produced the second.
    trace: [
      { tool: "web_search", status: "ok", turn: 1, args_summary: "query=alpha" },
      { tool: "scrape_docs", status: "ok", turn: 3, args_summary: "url=beta" },
    ],
  };

  it("renders each turn's work before the reply it produced, not pooled at the end", () => {
    const { container } = renderThread(null, twoTurns);
    const text = container.textContent;

    const firstWork = text.indexOf("Searched the web");
    const firstAnswer = text.indexOf("first answer");
    const secondWork = text.indexOf("Read documentation");
    const secondAnswer = text.indexOf("second answer");

    expect(firstWork).toBeGreaterThanOrEqual(0);
    expect(secondWork).toBeGreaterThanOrEqual(0);
    // Each summary sits with its own turn rather than both collecting below
    // the last message, which is what made a stale line hang at the bottom.
    expect(firstWork).toBeLessThan(firstAnswer);
    expect(firstAnswer).toBeLessThan(secondWork);
    expect(secondWork).toBeLessThan(secondAnswer);
  });

  it("counts only that turn's calls in its summary", () => {
    const { container } = renderThread(null, twoTurns);
    // Two separate one-call summaries, never one merged "2 steps" line.
    expect(container.textContent).not.toContain("2 steps");
  });
});

describe("assistant markdown", () => {
  it("renders a GFM table as a real table, not as pipe characters", () => {
    const { container } = renderThread(TABLE);

    const table = container.querySelector("table");
    expect(table, "a markdown table should render as <table>").toBeTruthy();
    expect(container.querySelectorAll("th")).toHaveLength(2);
    expect(container.querySelectorAll("tbody tr")).toHaveLength(2);
    expect(screen.getByText("AWS Bedrock")).toBeTruthy();

    // The raw delimiter row must not survive as visible text.
    expect(container.textContent).not.toContain("|---|");
  });

  it("keeps a wide table inside the message instead of stretching the thread", () => {
    const { container } = renderThread(TABLE);
    const scroller = container.querySelector(".md");
    expect(scroller.className).toContain("overflow-x-auto");
  });
});
