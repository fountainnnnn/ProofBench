import { describe, expect, it } from "vitest";
import {
  DETAIL_PREVIEW_CHARS,
  condenseDetail,
  summarizeTrace,
  traceSummaryText,
  traceTotals,
} from "./traceSummary.js";

describe("condenseDetail", () => {
  it("reduces scraped markup to plain text so a page cannot flood the view", () => {
    const scraped = `<html><head><style>body{color:red}</style><script>alert(1)</script></head>` +
      `<body><h1>Rate limits</h1><p>60 requests&nbsp;per minute</p></body></html>`;
    const result = condenseDetail(scraped);
    expect(result.text).toBe("Rate limits 60 requests per minute");
    expect(result.text).not.toContain("<");
    expect(result.text).not.toContain("alert(1)");
    expect(result.text).not.toContain("color:red");
  });

  it("bounds the preview and reports what is left over", () => {
    const long = "documentation ".repeat(400);
    const result = condenseDetail(long);
    expect(result.text.length).toBeLessThanOrEqual(DETAIL_PREVIEW_CHARS);
    expect(result.truncated).toBe(true);
    expect(result.hidden).toBeGreaterThan(0);
    expect(result.length).toBeGreaterThan(DETAIL_PREVIEW_CHARS);
  });

  it("leaves short detail untouched and handles missing values", () => {
    expect(condenseDetail("read docs")).toEqual({ text: "read docs", truncated: false, length: 9, hidden: 0 });
    expect(condenseDetail(null).text).toBe("");
    expect(condenseDetail(undefined).truncated).toBe(false);
  });

  it("bounds an expanded view as well, so nothing is unbounded", () => {
    const long = "x".repeat(50000);
    expect(condenseDetail(long, 2000).text).toHaveLength(2000);
  });
});

describe("summarizeTrace", () => {
  const trace = [
    { tool: "search_docs", status: "ok" },
    { tool: "scrape", status: "ok" },
    { tool: "scrape", status: "error" },
    { tool: "scrape", status: "start" },
    { tool: "search_docs", status: "ok" },
  ];

  it("groups by tool in first-appearance order with per-status counts", () => {
    const groups = summarizeTrace(trace);
    expect(groups.map((g) => g.tool)).toEqual(["search_docs", "scrape"]);
    expect(groups[0]).toMatchObject({ calls: 2, ok: 2, errors: 0, pending: 0 });
    expect(groups[1]).toMatchObject({ calls: 3, ok: 1, errors: 1, pending: 1 });
  });

  it("keeps the most recent calls per group and counts the rest instead of dropping them", () => {
    const many = Array.from({ length: 12 }, (_, i) => ({ tool: "scrape", status: "ok", detail: `call-${i}` }));
    const [group] = summarizeTrace(many, 4);
    expect(group.calls).toBe(12);
    expect(group.items).toHaveLength(4);
    expect(group.hidden).toBe(8);
    expect(group.items[3].detail).toBe("call-11");
  });

  it("treats an unknown status as in progress rather than as success", () => {
    const [group] = summarizeTrace([{ tool: "scrape" }, { tool: "scrape", status: "weird" }]);
    expect(group).toMatchObject({ calls: 2, ok: 0, errors: 0, pending: 2 });
  });

  it("tolerates missing or malformed input", () => {
    expect(summarizeTrace(null)).toEqual([]);
    expect(summarizeTrace([{}])[0].tool).toBe("step");
  });

  it("counts a start and its terminal as one call, not two", () => {
    const paired = [
      { tool: "scrape", status: "start", args_summary: "a" },
      { tool: "scrape", status: "start", args_summary: "b" },
      { tool: "scrape", status: "start", args_summary: "c" },
      { tool: "scrape", status: "ok", args_summary: "b", detail: "B done" },
      { tool: "scrape", status: "ok", args_summary: "c", detail: "C done" },
      { tool: "scrape", status: "ok", args_summary: "a", detail: "A done" },
    ];
    const [group] = summarizeTrace(paired);
    expect(group).toMatchObject({ calls: 3, ok: 3, errors: 0, pending: 0 });
    expect(group.items).toHaveLength(3);
    // start order is kept, and each call appears exactly once with its result
    expect(group.items.map((i) => i.args_summary)).toEqual(["a", "b", "c"]);
    expect(group.items.map((i) => i.detail)).toEqual(["A done", "B done", "C done"]);
  });

  it("falls back to FIFO when args cannot disambiguate", () => {
    const [group] = summarizeTrace([
      { tool: "scrape", status: "start", detail: "first" },
      { tool: "scrape", status: "start", detail: "second" },
      { tool: "scrape", status: "ok" },
    ]);
    expect(group).toMatchObject({ calls: 2, ok: 1, pending: 1 });
    expect(group.items[0]).toMatchObject({ status: "ok", detail: "first" });
    expect(group.items[1]).toMatchObject({ status: "start", detail: "second" });
  });

  it("keeps a standalone terminal and an unmatched start as separate calls", () => {
    const [group] = summarizeTrace([
      { tool: "scrape", status: "ok", detail: "done without a start" },
      { tool: "scrape", status: "start", detail: "still running" },
    ]);
    expect(group).toMatchObject({ calls: 2, ok: 1, errors: 0, pending: 1 });
    expect(group.items.map((i) => i.detail)).toEqual(["done without a start", "still running"]);
  });

  it("does not close a differently identified start", () => {
    const [group] = summarizeTrace([
      { tool: "scrape", status: "start", args_summary: "url=first" },
      { tool: "scrape", status: "ok", args_summary: "url=second" },
    ]);
    expect(group).toMatchObject({ calls: 2, ok: 1, pending: 1 });
  });

  it("pairs only within the same tool", () => {
    const groups = summarizeTrace([
      { tool: "scrape", status: "start" },
      { tool: "search_docs", status: "ok" },
    ]);
    expect(groups.map((g) => g.tool)).toEqual(["scrape", "search_docs"]);
    expect(groups[0]).toMatchObject({ calls: 1, pending: 1 });
    expect(groups[1]).toMatchObject({ calls: 1, ok: 1 });
  });
});

describe("traceSummaryText", () => {
  it("summarizes a run in one line", () => {
    expect(traceSummaryText([
      { tool: "a", status: "ok" },
      { tool: "b", status: "error" },
    ], 2)).toBe("2 tool calls across 2 tools, 2 sandboxes, 1 error");
  });

  it("uses singular forms and omits empty parts", () => {
    expect(traceSummaryText([{ tool: "a", status: "ok" }], 1)).toBe("1 tool call across 1 tool, 1 sandbox");
    expect(traceSummaryText([], 0)).toBeNull();
  });

  it("totals logical calls across groups, not raw events", () => {
    // a's start and error are one failed call, so this is 2 calls, not 3 events
    expect(traceTotals([{ tool: "a" }, { tool: "a", status: "error" }, { tool: "b", status: "ok" }]))
      .toEqual({ tools: 2, calls: 2, errors: 1 });
  });
});
