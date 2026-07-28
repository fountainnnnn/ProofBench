// The agent trace carries whatever the documentation tools scraped. A single
// detail string can be a whole HTML page, so nothing here is rendered at its
// original length: markup is reduced to its text, the text is bounded, and the
// caller decides when to spend more space on it.

export const DETAIL_PREVIEW_CHARS = 220;
export const DETAIL_EXPANDED_CHARS = 2000;
export const GROUP_ITEM_LIMIT = 8;

const SCRIPT_OR_STYLE = /<(script|style)\b[^>]*>[\s\S]*?<\/\1>/gi;
const TAG = /<[^<>]{0,2000}>/g;
const ENTITY = /&(?:nbsp|amp|lt|gt|quot|apos|#\d{1,6}|#x[0-9a-f]{1,6});/gi;

/**
 * Reduce one raw trace detail to bounded plain text.
 * Returns the visible slice plus what was dropped, so the UI can offer a
 * deliberate expansion instead of silently truncating.
 */
export function condenseDetail(value, limit = DETAIL_PREVIEW_CHARS) {
  const text = String(value ?? "")
    .replace(SCRIPT_OR_STYLE, " ")
    .replace(TAG, " ")
    .replace(ENTITY, " ")
    .replace(/\s+/g, " ")
    .trim();
  const bound = Math.max(0, limit);
  if (text.length <= bound) {
    return { text, truncated: false, length: text.length, hidden: 0 };
  }
  return {
    text: text.slice(0, bound).trimEnd(),
    truncated: true,
    length: text.length,
    hidden: text.length - bound,
  };
}

function statusOf(item) {
  const status = String(item?.status || "start").toLowerCase();
  return status === "ok" || status === "error" ? status : "start";
}

function argsKey(item) {
  const args = item?.args_summary;
  if (args === undefined || args === null || args === "") return null;
  return String(args);
}

/** Terminal fields win, but only where the terminal actually supplies them. */
function mergeTerminal(start, terminal) {
  const merged = { ...start };
  Object.entries(terminal).forEach(([key, value]) => {
    if (value !== undefined && value !== null) merged[key] = value;
  });
  return merged;
}

/**
 * Collapse raw start/terminal events into logical calls.
 * A terminal (ok/error) closes one earlier still-open start for the same tool:
 * the one with the same args_summary when both sides supply it, otherwise the
 * oldest open start. A terminal with nothing to close is a completed call on
 * its own; a start nobody closed stays pending.
 */
export function normalizeTraceCalls(trace) {
  const calls = [];
  const openByTool = new Map();
  (Array.isArray(trace) ? trace : []).forEach((item, index) => {
    const tool = String(item?.tool || "step");
    const status = statusOf(item);
    if (status === "start") {
      calls.push({ ...item, tool, status, index });
      if (!openByTool.has(tool)) openByTool.set(tool, []);
      openByTool.get(tool).push(calls.length - 1);
      return;
    }
    const open = openByTool.get(tool) || [];
    const key = argsKey(item);
    let slot = key === null ? -1 : open.findIndex((i) => argsKey(calls[i]) === key);
    // If both records name different arguments, they are different calls.
    // We may only use FIFO for the starts that supplied no summary at all.
    if (slot === -1 && key !== null) slot = open.findIndex((i) => argsKey(calls[i]) === null);
    if (slot === -1 && key === null && open.length > 0) slot = 0;
    if (slot === -1) {
      calls.push({ ...item, tool, status, index });
      return;
    }
    const target = open[slot];
    open.splice(slot, 1);
    calls[target] = { ...mergeTerminal(calls[target], item), tool, status, index: calls[target].index };
  });
  return calls;
}

/**
 * Group logical calls by tool so a fifty-call run reads as a handful of steps.
 * Order follows first appearance, and each group keeps only its most recent
 * calls; the rest are counted, never dropped silently.
 */
export function summarizeTrace(trace, itemLimit = GROUP_ITEM_LIMIT) {
  const groups = new Map();
  normalizeTraceCalls(trace).forEach((call, index) => {
    const tool = call.tool;
    if (!groups.has(tool)) {
      groups.set(tool, { tool, firstIndex: index, calls: 0, ok: 0, errors: 0, pending: 0, items: [] });
    }
    const group = groups.get(tool);
    group.calls += 1;
    if (call.status === "ok") group.ok += 1;
    else if (call.status === "error") group.errors += 1;
    else group.pending += 1;
    group.items.push(call);
  });
  return [...groups.values()]
    .sort((a, b) => a.firstIndex - b.firstIndex)
    .map((group) => ({
      ...group,
      hidden: Math.max(0, group.items.length - itemLimit),
      items: group.items.slice(-itemLimit),
    }));
}

/** One-line counts for a collapsed trace header. */
export function traceTotals(trace) {
  const groups = summarizeTrace(trace);
  return groups.reduce(
    (totals, group) => ({
      tools: totals.tools + 1,
      calls: totals.calls + group.calls,
      errors: totals.errors + group.errors,
    }),
    { tools: 0, calls: 0, errors: 0 }
  );
}

/* What each tool is DOING, in the reader's language rather than the tool's.
   "Searched the web" tells an operator what happened; "web_search: 3 calls"
   makes them translate. Anything unmapped falls back to its own name with the
   underscores opened out, so a new tool degrades to something readable rather
   than to nothing. */
/* `verb`/`gerund` head the group line; `act`/`acting` prefix each individual
   call, so a row reads as the action it performed ("Read docs.aws.amazon.com")
   rather than as a bare address. */
const TOOL_VERBS = {
  // "Searched", not "Found": the row names a page the search reached, and
  // "Found <domain>" read as though the domain itself were the discovery.
  web_search: { verb: "Searched the web", gerund: "Searching the web", act: "Searched", acting: "Searching for", noun: "search", plural: "searches" },
  fetch_url: { verb: "Read", gerund: "Reading", act: "Read", acting: "Opening", noun: "page", plural: "pages" },
  fetch_page: { verb: "Read", gerund: "Reading", act: "Read", acting: "Opening", noun: "page", plural: "pages" },
  scrape: { verb: "Read", gerund: "Reading", act: "Read", acting: "Opening", noun: "page", plural: "pages" },
  scrape_docs: { verb: "Read documentation", gerund: "Reading documentation", act: "Read", acting: "Opening", noun: "page", plural: "pages" },
  docs_intel: { verb: "Read documentation", gerund: "Reading documentation", act: "Read", acting: "Opening", noun: "document", plural: "documents" },
  run_sandbox: { verb: "Ran in sandbox", gerund: "Running in sandbox", act: "Ran", acting: "Running", noun: "run", plural: "runs" },
  build_adapter: { verb: "Built adapters", gerund: "Building adapters", act: "Built", acting: "Building", noun: "adapter", plural: "adapters" },
  evaluate: { verb: "Scored output", gerund: "Scoring output", act: "Scored", acting: "Scoring", noun: "evaluation", plural: "evaluations" },
  // The batch call's own summary already says what was assessed ("6
  // implementation assessments via doubleword"), so its rows take no action
  // prefix at all — a prefix would restate the header.
  assess_documentation_batch: { verb: "Assessed documentation", gerund: "Assessing documentation", act: "", acting: "", noun: "batch", plural: "batches" },
  assess_implementation: { verb: "Rated candidates", gerund: "Rating candidates", act: "Rated", acting: "Rating", noun: "candidate", plural: "candidates" },
  shortlist_review: { verb: "Reviewed the shortlist", gerund: "Reviewing the shortlist", act: "", acting: "", noun: "review", plural: "reviews" },
  // One call, once per session, so its rows take no action prefix either — the
  // header sentence is the whole story.
  prompt_brief: { verb: "Prepared the research brief", gerund: "Preparing the research brief", act: "", acting: "", noun: "brief", plural: "briefs" },
};

const openWords = (tool) => String(tool || "step").replace(/[_-]+/g, " ").trim();

export function toolVerbs(tool) {
  return (
    TOOL_VERBS[tool] || {
      verb: openWords(tool),
      gerund: openWords(tool),
      // The group header already names the tool; repeating that name as each
      // row's action prefix printed "assess documentation batch" twice in a
      // row. An unmapped tool's rows carry their summary alone.
      act: "",
      acting: "",
      noun: "call",
      plural: "calls",
    }
  );
}

/** The query a call was made with, when it carried one. */
export function callQuery(call) {
  const raw = typeof call?.args_summary === "string" ? call.args_summary : "";
  const match = raw.match(/(?:query|q)\s*=\s*(.+)$/i);
  return match ? match[1].trim() : "";
}

/**
 * One human sentence per tool for a settled run: "Searched the web · 3
 * searches". Used by the collapsed activity row a reader clicks to open the
 * full log, so it must read as prose, not as telemetry.
 */
export function activityPhrases(trace) {
  return summarizeTrace(trace).map((group) => {
    const words = toolVerbs(group.tool);
    const count = group.calls;
    return {
      tool: group.tool,
      text: `${words.verb} · ${count} ${count === 1 ? words.noun : words.plural}`,
      short: words.verb,
      count,
      errors: group.errors,
    };
  });
}

/** The single line a settled run collapses to, e.g. "Searched the web · 3 searches". */
export function activitySummaryText(trace) {
  const phrases = activityPhrases(trace);
  if (phrases.length === 0) return null;
  if (phrases.length === 1) return phrases[0].text;
  const { calls } = traceTotals(trace);
  return `${phrases.map((p) => p.short).join(" · ")} · ${calls} steps`;
}

/* Pull the pages a run actually visited out of the trace.
   Tool payloads carry URLs two ways: an `args_summary` like "url=https://…"
   for a fetch, and a JSON result blob listing {title, url, snippet} for a
   search. Both are mined here so the log can be shown as the SITES consulted
   rather than as the raw arguments — a reader checking evidence wants the
   sources, not the call signature. */
const URL_RE = /https?:\/\/[^\s"'<>)\]]+/g;

const cleanUrl = (raw) => String(raw || "").replace(/[.,;:]+$/, "");

export function traceSources(trace) {
  const byUrl = new Map();

  const add = (url, title) => {
    const clean = cleanUrl(url);
    if (!/^https?:\/\//i.test(clean)) return;
    let host;
    try {
      host = new URL(clean).hostname.replace(/^www\./, "");
    } catch {
      return;
    }
    const existing = byUrl.get(clean);
    if (existing) {
      if (!existing.title && title) existing.title = title;
      return;
    }
    byUrl.set(clean, { url: clean, host, title: title || "" });
  };

  for (const item of Array.isArray(trace) ? trace : []) {
    const blobs = [item?.args_summary, item?.summary, item?.detail, item?.result];
    for (const blob of blobs) {
      if (!blob) continue;
      const text = typeof blob === "string" ? blob : JSON.stringify(blob);
      /* Prefer structured {title,url} pairs so a result keeps its headline;
         fall back to bare URLs found anywhere in the payload. */
      for (const match of text.matchAll(/"title"\s*:\s*"((?:[^"\\]|\\.)*)"\s*,\s*"url"\s*:\s*"([^"]+)"/g)) {
        add(match[2], match[1].replace(/\\"/g, '"'));
      }
      for (const match of text.matchAll(/"url"\s*:\s*"([^"]+)"\s*,\s*"title"\s*:\s*"((?:[^"\\]|\\.)*)"/g)) {
        add(match[1], match[2].replace(/\\"/g, '"'));
      }
      for (const url of text.match(URL_RE) || []) add(url, "");
    }
  }

  return [...byUrl.values()];
}

/** "12 tool calls across 4 tools, 1 error" style summary, or null when empty. */
export function traceSummaryText(trace, sandboxCount = 0) {
  const { tools, calls, errors } = traceTotals(trace);
  const parts = [];
  if (calls > 0) parts.push(`${calls} tool ${calls === 1 ? "call" : "calls"} across ${tools} ${tools === 1 ? "tool" : "tools"}`);
  if (sandboxCount > 0) parts.push(`${sandboxCount} ${sandboxCount === 1 ? "sandbox" : "sandboxes"}`);
  if (errors > 0) parts.push(`${errors} ${errors === 1 ? "error" : "errors"}`);
  return parts.length > 0 ? parts.join(", ") : null;
}
