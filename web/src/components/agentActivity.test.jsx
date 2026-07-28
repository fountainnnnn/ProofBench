// @vitest-environment jsdom
//
// Watching a run should read as narration, not as a list of addresses. Each row
// names the action it performed and its target, and the tense follows whether
// the call has returned.
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import AgentActivity from "./AgentActivity.jsx";

afterEach(cleanup);

const TRACE = [
  {
    tool: "web_search",
    status: "ok",
    args_summary: "query=best RAG cloud services",
    detail: '[{"title": "RAG tools", "url": "https://pinecone.io/blog/rag"}]',
  },
  {
    tool: "scrape_docs",
    status: "ok",
    args_summary: "url=https://docs.aws.amazon.com/bedrock",
  },
  {
    tool: "scrape_docs",
    status: "start",
    args_summary: "url=https://learn.microsoft.com/azure",
  },
];

describe("rows name the page, not just its address", () => {
  it("says what a search reached rather than announcing the domain", () => {
    render(<AgentActivity trace={TRACE} live onOpenLog={vi.fn()} />);

    // "Found pinecone.io" read as though the domain were the discovery.
    expect(screen.queryByText(/^Found$/)).toBeNull();
    expect(screen.getAllByText(/^Searched$/).length).toBeGreaterThan(0);
  });

  it("leads with the page headline and keeps the site beside it", () => {
    render(<AgentActivity trace={TRACE} live onOpenLog={vi.fn()} />);

    // A bare host says a search touched pinecone.io; the title says what it read.
    expect(screen.getByText(/RAG tools/)).toBeTruthy();
    expect(screen.getByText(/pinecone\.io/)).toBeTruthy();
  });

  it("falls back to the host when a result carried no headline", () => {
    render(<AgentActivity trace={TRACE} live onOpenLog={vi.fn()} />);
    expect(screen.getByText(/docs\.aws\.amazon\.com/)).toBeTruthy();
  });

  it("names what a still-running call asked for, having reached nothing yet", () => {
    render(<AgentActivity trace={TRACE} live onOpenLog={vi.fn()} />);
    expect(screen.getByText(/learn\.microsoft\.com/)).toBeTruthy();
  });
});

describe("live activity narrates the work", () => {
  it("prefixes each row with the action it performed, and its target", () => {
    render(<AgentActivity trace={TRACE} live onOpenLog={vi.fn()} />);

    // A returned fetch reads in the past tense, with the site it read.
    expect(screen.getByText(/^Read$/)).toBeTruthy();
    expect(screen.getByText(/docs\.aws\.amazon\.com/)).toBeTruthy();

    // A call still in flight reads as in progress.
    expect(screen.getByText(/^Opening$/)).toBeTruthy();
    expect(screen.getByText(/learn\.microsoft\.com/)).toBeTruthy();
  });

  it("heads each group with the tool's own sentence and a count", () => {
    render(<AgentActivity trace={TRACE} live onOpenLog={vi.fn()} />);
    expect(screen.getByText(/Searched the web/)).toBeTruthy();
    // Two scrape calls, one of them still running, so the group is in progress.
    expect(screen.getByText(/Reading documentation/)).toBeTruthy();
    expect(screen.getByText(/2 pages/)).toBeTruthy();
  });

  it("collapses to the number of sites consulted once the run settles", () => {
    render(<AgentActivity trace={TRACE} onOpenLog={vi.fn()} />);
    // Three distinct hosts across the trace.
    expect(screen.getByRole("button", { name: /Searched 3 sites on the web/ })).toBeTruthy();
    // The verbose rows are gone.
    expect(screen.queryByText(/^Opening$/)).toBeNull();
  });
});
