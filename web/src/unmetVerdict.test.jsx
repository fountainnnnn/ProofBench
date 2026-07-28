// @vitest-environment jsdom
//
// A ranking is not a recommendation.
//
// One real run assessed five tools for generating math questions *with
// diagrams*. Every one was rated not implementable, each reason naming that
// same unmet requirement — and the console still headlined
// "Recommendation: ace_quiz, 49/100, ranked first of 5". Relative order among
// failures is not an endorsement of the least-bad one.
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { buildCanonicalRows, buildDecision, componentRows } from "./resultsModel.js";
import ResultsCard from "./components/ResultsCard.jsx";

vi.mock("./api.js", () => ({ prepareReportPdf: vi.fn() }));

afterEach(cleanup);

const ALL_FAILED = {
  ace_quiz: { rating: 49, implementable: false, display_name: "Ace Quiz",
              reason: "The documentation does not mention support for generating diagrams, which is a hard requirement." },
  varsity_tutors: { rating: 28, implementable: false, display_name: "Varsity Tutors",
                    reason: "No diagram support." },
  wolfram_alpha: { rating: 13, implementable: false, display_name: "Wolfram Alpha",
                   reason: "Insufficient integration detail." },
};

const ONE_VIABLE = {
  ...ALL_FAILED,
  good_tool: { rating: 81, implementable: true, display_name: "Good Tool", reason: "Documents diagrams." },
};

const decide = (metrics) => buildDecision(
  buildCanonicalRows(metrics, true), true, componentRows(metrics, true));

describe("a field where nothing met the requirements", () => {
  it("is flagged rather than crowned", () => {
    const decision = decide(ALL_FAILED);
    expect(decision.unmet).toBe(true);
    expect(decision.failedCount).toBe(3);
  });

  it("still ranks the field, because relative order is real information", () => {
    // The table below the verdict is unchanged; only the claim above it is.
    expect(decide(ALL_FAILED).winner.name).toBe("ace_quiz");
  });

  it("recommends normally as soon as one candidate is implementable", () => {
    const decision = decide(ONE_VIABLE);
    expect(decision.unmet).toBe(false);
    expect(decision.winner.name).toBe("good_tool");
  });

  it("treats an unknown implementable flag as no objection", () => {
    // Extraction runs and legacy rows carry no flag; they must not all become
    // "no recommendation".
    const decision = decide({ a: { rating: 70 }, b: { rating: 40 } });
    expect(decision.unmet).toBe(false);
  });

  it("judges on products, not on a build component topping the table", () => {
    const decision = decide({
      ...ALL_FAILED,
      helper_lib: { rating: 88, implementable: true, role: "build_component",
                    display_name: "Helper Lib", reason: "Well documented." },
    });
    expect(decision.unmet).toBe(true);
    expect(decision.buildPath.map((row) => row.name)).toEqual(["helper_lib"]);
  });
});

describe("the verdict card", () => {
  const renderCard = (metrics) => render(
    <ResultsCard
      metrics={metrics}
      report={null}
      runId="run-1"
      phase="DONE"
      running={false}
      executionMode="documentation_evidence"
    />
  );

  it("says no recommendation instead of naming a winner", () => {
    renderCard(ALL_FAILED);

    expect(screen.getByText("No recommendation")).toBeTruthy();
    expect(screen.getByText(/No candidate met the requirements/)).toBeTruthy();
    expect(screen.queryByText("Recommendation")).toBeNull();
  });

  it("quotes the requirement that went unmet, which is the actionable part", () => {
    renderCard(ALL_FAILED);
    expect(screen.getByText(/hard requirement/)).toBeTruthy();
  });

  it("counts how many failed against how many were scored", () => {
    renderCard(ALL_FAILED);
    expect(screen.getByText(/3 of 3 candidates were rated not implementable/)).toBeTruthy();
  });

  it("still recommends when something actually qualifies", () => {
    renderCard(ONE_VIABLE);
    expect(screen.getByText("Recommendation")).toBeTruthy();
    // Named in the verdict and again in the ranked table below it.
    expect(screen.getAllByText("Good Tool").length).toBeGreaterThan(0);
  });
});

// Ordering: nothing else matters if the requirement is unmet.
describe("requirement fit partitions the ranking", () => {
  it("puts a capable tool above a higher-scoring one that cannot do the job", () => {
    // The measured inversion: failing caps a rating at 49, which beat a capable 43.
    const rows = buildCanonicalRows({
      polished: { rating: 49, implementable: false, display_name: "Polished" },
      capable: { rating: 43, implementable: true, display_name: "Capable" },
    }, true);

    expect(rows.find((r) => r.canonicalRank === 1).name).toBe("capable");
    expect(rows.find((r) => r.name === "polished").canonicalRank).toBe(2);
  });

  it("still orders by score inside each group", () => {
    const rows = buildCanonicalRows({
      weak_ok: { rating: 51, implementable: true },
      strong_ok: { rating: 90, implementable: true },
      bad_hi: { rating: 49, implementable: false },
      bad_lo: { rating: 10, implementable: false },
    }, true);

    expect(rows.slice().sort((a, b) => a.canonicalRank - b.canonicalRank).map((r) => r.name))
      .toEqual(["strong_ok", "weak_ok", "bad_hi", "bad_lo"]);
  });

  it("leaves extraction rankings alone, since they carry no flag", () => {
    const rows = buildCanonicalRows({ a: { exact_accuracy: 0.9 }, b: { exact_accuracy: 0.7 } });
    expect(rows.find((r) => r.canonicalRank === 1).name).toBe("a");
  });
});

// A build component is a part, not a rival product.
describe("components do not compete with products", () => {
  const MIXED = {
    matplotlib: { rating: 100, implementable: true, role: "build_component", display_name: "Matplotlib" },
    sympy: { rating: 100, implementable: true, role: "build_component", display_name: "SymPy" },
    creately: { rating: 49, implementable: false, role: "product", display_name: "Creately" },
    edraw: { rating: 19, implementable: false, role: "product", display_name: "Edraw" },
  };

  it("never gives first place to a library over the products", () => {
    // Asked what generates math questions, the table answered "1. Matplotlib".
    const rows = buildCanonicalRows(MIXED, true);
    expect(rows.find((r) => r.canonicalRank === 1).name).toBe("creately");
  });

  it("keeps libraries out of the results table entirely", () => {
    // Benchmark results are for products. A library is a part of one self-built
    // architecture, and the report's plan is where that architecture is shown —
    // an unranked row here just reads as an entry that lost.
    const rows = buildCanonicalRows(MIXED, true);

    expect(rows.map((r) => r.name).sort()).toEqual(["creately", "edraw"]);
  });

  it("reports no recommendation when every product failed", () => {
    const decision = buildDecision(
      buildCanonicalRows(MIXED, true), true, componentRows(MIXED, true));

    expect(decision.unmet).toBe(true);
    expect(decision.buildPath.map((r) => r.name).sort()).toEqual(["matplotlib", "sympy"]);
  });

  it("loses the build path when the components are not passed in", () => {
    // The libraries are absent from the rows now, so a caller that forgets them
    // gets an unmet verdict with nowhere to go — which is why both call sites
    // read them from the metrics.
    expect(buildDecision(buildCanonicalRows(MIXED, true), true).buildPath).toEqual([]);
  });
});
