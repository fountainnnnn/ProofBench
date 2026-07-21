import { describe, expect, it } from "vitest";
import {
  buildCanonicalRows,
  classifyResultsState,
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
    })).toBe("Verified in Daytona");
  });
});
