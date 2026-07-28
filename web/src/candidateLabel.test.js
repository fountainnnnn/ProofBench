// A verdict about "Azure AI Search + Azure OpenAI" that calls it
// `azure_ai_search_openai` reads like a database dump. The slug is the row's
// identity — sort keys, React keys, the brand-logo manifest — so the readable
// name has to ride alongside it rather than replace it.
import { describe, expect, it } from "vitest";
import { buildCanonicalRows, candidateLabel } from "./resultsModel.js";

const METRICS = {
  azure_ai_search_openai: { rating: 92, display_name: "Azure AI Search + Azure OpenAI" },
  langchain_sharepoint: { rating: 61, display_name: "LangChain + SharePointLoader" },
  legacy_run_tool: { rating: 30 },
};

describe("candidate labels", () => {
  it("prefers the vendor's own name", () => {
    expect(candidateLabel({ name: "customgpt", display_name: "CustomGPT.ai" })).toBe("CustomGPT.ai");
  });

  it("falls back to the slug when a run predates display names", () => {
    expect(candidateLabel({ name: "legacy_run_tool" })).toBe("legacy_run_tool");
  });

  it("survives a row that is missing entirely", () => {
    expect(candidateLabel(null)).toBe("");
  });

  it("is attached to every canonical row", () => {
    const rows = buildCanonicalRows(METRICS, true);
    expect(rows.map((row) => row.label)).toEqual([
      "Azure AI Search + Azure OpenAI",
      "LangChain + SharePointLoader",
      "legacy_run_tool",
    ]);
  });

  it("leaves `name` as the slug, because identity depends on it", () => {
    const rows = buildCanonicalRows(METRICS, true);
    // Brand logos are keyed by the slug; relabelling the row would lose them.
    expect(rows[0].name).toBe("azure_ai_search_openai");
    expect(rows[0].isWinner).toBe(true);
  });
});
