// @vitest-environment jsdom
//
// The search log is scaffolding for an answer, not the answer. Once the reply
// starts arriving it must fold to one line, or the reader scrolls past a
// finished list of URLs to reach the thing they asked for.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import ChatThread from "./ChatThread.jsx";

vi.mock("../api.js", () => ({ prepareReportPdf: vi.fn() }));

const TRACE = [
  { turn: 1, tool: "web_search", status: "ok", args_summary: "query=rag over sharepoint",
    detail: '[{"title": "Azure AI Search", "url": "https://learn.microsoft.com/azure/search"}]' },
  { turn: 1, tool: "scrape_docs", status: "ok", args_summary: "url=https://docs.aws.amazon.com/bedrock" },
];

function renderThread(overrides = {}) {
  return render(
    <ChatThread
      messages={[{ role: "user", text: "RAG chatbot over SharePoint?" }]}
      trace={TRACE}
      sandboxLogs={{}}
      phaseState={{ phase: "INTAKE" }}
      spec={null}
      results={null}
      report={null}
      specProvenance={null}
      resultsProvenance={null}
      runId={null}
      onRun={vi.fn()}
      onStop={vi.fn()}
      running={false}
      stopping={false}
      typing
      {...overrides}
    />
  );
}

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(cleanup);

const answering = (text) => ({
  messages: [
    { role: "user", text: "RAG chatbot over SharePoint?" },
    { role: "assistant", text, streaming: true },
  ],
});

describe("the search log folds away when the answer arrives", () => {
  it("stays expanded while the agent is still working and has said nothing", () => {
    renderThread();
    expect(screen.getByText(/Searched the web/)).toBeTruthy();
    expect(screen.getByText(/docs\.aws\.amazon\.com/)).toBeTruthy();
  });

  it("collapses as soon as the reply begins streaming, not when the turn ends", () => {
    renderThread(answering("Great, I now have a solid picture."));

    // One quiet line, offering the detail rather than displaying it.
    expect(screen.getByRole("button", { name: /Searched 2 sites on the web/ })).toBeTruthy();
    // The per-call rows are gone from above the answer.
    expect(screen.queryByText(/docs\.aws\.amazon\.com/)).toBeNull();
  });

  it("keeps the log open for a turn that is working but has produced no text yet", () => {
    renderThread(answering(""));
    expect(screen.getByText(/Searched the web/)).toBeTruthy();
  });

  it("orders the collapsed line above the answer it informed", () => {
    const { container } = renderThread(answering("Great, I now have a solid picture."));
    const text = container.textContent;
    expect(text.indexOf("Searched 2 sites")).toBeLessThan(text.indexOf("solid picture"));
  });
});
