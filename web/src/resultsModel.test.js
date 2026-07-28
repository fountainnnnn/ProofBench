import { describe, expect, it } from "vitest";
import {
  buildCanonicalRows,
  buildDecision,
  classifyResultsState,
  evidenceTier,
  executionBasisLabel,
  sortResultRows,
} from "./resultsModel.js";

describe("result ranking", () => {
  it("keeps canonical rank stable when display sorting changes", () => {
    const canonical = buildCanonicalRows({
      alpha: { exact_accuracy: 0.91, mean_latency_s: 4 },
      beta: { exact_accuracy: 0.82, mean_latency_s: 1 },
    });
    const byLatency = sortResultRows(canonical, "mean_latency_s", "asc");
    expect(byLatency.map((row) => row.name)).toEqual(["beta", "alpha"]);
    expect(byLatency.find((row) => row.name === "alpha")).toMatchObject({ canonicalRank: 1, isWinner: true });
  });

  it("never ranks a missing or non-finite primary metric", () => {
    const rows = buildCanonicalRows({
      missing: { exact_accuracy: null },
      invalid: { exact_accuracy: Number.NaN },
      valid: { exact_accuracy: 0 },
    });
    expect(rows.find((row) => row.name === "valid")).toMatchObject({ canonicalRank: 1, isWinner: true });
    expect(rows.filter((row) => row.isWinner)).toHaveLength(1);
    expect(rows.find((row) => row.name === "missing").canonicalRank).toBeNull();
  });

  it("has no winner when every primary metric is missing", () => {
    const rows = buildCanonicalRows({ a: {}, b: { exact_accuracy: null } });
    expect(rows.every((row) => row.canonicalRank === null && !row.isWinner)).toBe(true);
  });

  it("classifies terminal missing and non-finite metrics honestly", () => {
    expect(classifyResultsState(null, "DONE")).toBe("empty");
    expect(classifyResultsState({ alpha: { exact_accuracy: Number.NaN } }, "DONE")).toBe("unavailable");
    expect(classifyResultsState(null, "FAILED")).toBe("failed");
    expect(classifyResultsState(null, "STOPPED")).toBe("stopped");
    expect(classifyResultsState(null, "RUNNING", true)).toBe("loading");
  });

  it("renders backend assessment basis as user-facing evidence language", () => {
    expect(executionBasisLabel({
      cloud: {
        execution_mode: "comparison_only",
        assessment_basis: "documentation_evidence",
        daytona_triggered: false,
      },
    })).toBe("Compared from documentation");
    expect(executionBasisLabel({
      local: {
        execution_mode: "sandbox_verifiable",
        assessment_basis: "sandbox_execution",
        daytona_triggered: true,
      },
    })).toBe("Verified by execution");
  });
});

describe("a candidate scored from its documentation", () => {
  const mixed = {
    tesseract: { exact_accuracy: 0.667, status: "ok", research_score: 55 },
    veryfi: { exact_accuracy: 0.0, status: "ok", research_score: 91 },
    affinda: {
      exact_accuracy: null,
      status: "no_result",
      error_summary: "AFFINDA_API_KEY environment variable not set",
      research_score: 88,
    },
    mindee: { exact_accuracy: null, status: "no_result", research_score: 74 },
    easyocr: { exact_accuracy: null, status: "no_result" },
  };

  it("is ranked, so a run that could not reach it still compares it", () => {
    const rows = buildCanonicalRows(mixed);
    const rank = Object.fromEntries(rows.map((row) => [row.name, row.canonicalRank]));
    expect(rank.affinda).toBe(3);
    expect(rank.mindee).toBe(4);
  });

  it("never outranks a candidate that was actually measured", () => {
    const rows = buildCanonicalRows(mixed);
    const rank = Object.fromEntries(rows.map((row) => [row.name, row.canonicalRank]));
    // veryfi measured 0% and affinda's documentation scores 88. The measurement
    // still wins: it is the only one of the two this run can vouch for.
    expect(rank.veryfi).toBeLessThan(rank.affinda);
    expect(rows.find((row) => row.name === "tesseract").isWinner).toBe(true);
  });

  it("stays unranked when there is no evidence of any kind", () => {
    const rows = buildCanonicalRows(mixed);
    expect(rows.find((row) => row.name === "easyocr").canonicalRank).toBeNull();
  });

  it("reports which evidence each tier rests on", () => {
    expect(evidenceTier({ exact_accuracy: 0.5 })).toBe("measured");
    expect(evidenceTier({ exact_accuracy: null, research_score: 80 })).toBe("research");
    expect(evidenceTier({ exact_accuracy: null })).toBe("none");
  });

  it("does not let a documentation opinion demote a measurement", () => {
    // The docs assessment says this product cannot meet the requirement, but the
    // sandbox measured it doing the job. The number wins.
    const rows = buildCanonicalRows({
      measured: { exact_accuracy: 0.9, implementable: false, research_score: 30 },
      documented: { exact_accuracy: null, implementable: true, research_score: 95 },
    });
    expect(rows.find((row) => row.name === "measured").canonicalRank).toBe(1);
  });
});

describe("a verdict with no measurement behind it", () => {
  const documentedOnly = {
    affinda: { exact_accuracy: null, status: "no_result", research_score: 88 },
    mindee: { exact_accuracy: null, status: "no_result", research_score: 74 },
  };

  it("is stated on the documentation score, not on an accuracy nothing measured", () => {
    const decision = buildDecision(buildCanonicalRows(documentedOnly));
    expect(decision.evidence).toBe("research");
    expect(decision.key).toBe("research_score");
    expect(decision.winner.name).toBe("affinda");
    expect(decision.margin).toBe(14);
  });

  it("claims no margin between a measured winner and a documented runner up", () => {
    const decision = buildDecision(buildCanonicalRows({
      tesseract: { exact_accuracy: 0.667, research_score: 55 },
      affinda: { exact_accuracy: null, research_score: 88 },
    }));
    expect(decision.evidence).toBe("measured");
    expect(decision.margin).toBeNull();
    expect(decision.measuredCount).toBe(1);
    expect(decision.researchCount).toBe(1);
  });
});
