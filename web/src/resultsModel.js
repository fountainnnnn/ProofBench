export function primaryMetricKey(isAssessment) {
  return isAssessment ? "rating" : "exact_accuracy";
}

function isFiniteMetric(value) {
  return typeof value === "number" && Number.isFinite(value);
}

export function classifyResultsState(metrics, phase, running = false) {
  if (running) return "loading";
  const terminal = String(phase || "").toUpperCase();
  if (terminal === "FAILED") return "failed";
  if (terminal === "STOPPED") return "stopped";
  if (!metrics || typeof metrics !== "object" || Object.keys(metrics).length === 0) {
    return terminal === "DONE" ? "empty" : "idle";
  }
  const assessment = Object.values(metrics).some((row) => row?.rating !== undefined);
  const primary = primaryMetricKey(assessment);
  /* A documentation score is a result too. A run where every candidate was
     blocked from executing still has something to show, and reporting "no
     finite primary score" over a full set of documentation evidence hid a
     comparison the run had already done. */
  return Object.values(metrics).some(
    (row) => isFiniteMetric(row?.[primary]) || isFiniteMetric(row?.research_score)
  ) ? "ready" : "unavailable";
}

/**
 * What to show a person. `name` stays the slug because it is the row's identity
 * — sort keys, React keys, and the brand-logo manifest are all keyed by it — so
 * the readable name rides alongside rather than replacing it.
 */
export function candidateLabel(row) {
  return String(row?.display_name || row?.name || "");
}

function toRows(metrics) {
  return Object.entries(metrics || {}).map(([name, values]) => ({
    name, ...values, label: candidateLabel({ name, ...values }),
  }));
}

/**
 * The assessed build components, which are not benchmark entries.
 * A library is a part of ONE self-built architecture, not a rival product, so
 * it never becomes a row in a results table. The decision still needs them —
 * they are what "build it yourself" is built from — so they are read from the
 * metrics directly rather than from the rows the table renders.
 */
export function componentRows(metrics, isAssessment = false) {
  const primaryKey = primaryMetricKey(isAssessment);
  return toRows(metrics).filter(
    (row) => row.role === "build_component" && isFiniteMetric(row[primaryKey]));
}

/**
 * The benchmark entries: products only, ranked. Components are excluded
 * entirely rather than returned unranked — an unranked row still reads as an
 * entry that lost, and a library was never competing.
 */
/**
 * How strong the evidence behind a row is. Measurement beats documentation,
 * always: a candidate the sandbox actually ran is the only kind this product
 * can vouch for, and a well-written documentation set is not allowed to outrank
 * one. Both beat nothing, which is what an unreachable candidate used to be.
 */
export function evidenceTier(row, isAssessment = false) {
  if (isFiniteMetric(row?.[primaryMetricKey(isAssessment)])) return "measured";
  if (isFiniteMetric(row?.research_score)) return "research";
  return "none";
}

export function buildCanonicalRows(metrics, isAssessment = false) {
  const primaryKey = primaryMetricKey(isAssessment);
  const rows = toRows(metrics).filter((row) => row.role !== "build_component");
  /* Requirement fit partitions the field; score only orders within it.
     Failing the requirement caps a rating at 49, which still beat a capable
     tool scoring 43 on thinner documentation — so a library that could not do
     the job ranked above one that could. Nothing else matters when the
     requirement is unmet. Only an explicit false is a failure: extraction rows
     and older assessments carry no flag, and an absent flag is not a demotion. */
  const meetsRequirements = (row) => (row?.implementable === false ? 0 : 1);
  /* Only products are ranked, and only products are here at all.
     Ranking the two together produced nonsense: asked what generates math
     questions with diagrams, the table answered "1. Matplotlib, 100/100" —
     a plotting library that generates no questions at all. It scored 100 for
     having excellent documentation and passing an import check, which is true
     and irrelevant. The ranking answers "which product should I adopt"; the
     report's plan answers "what if none of them do". */
  /* A candidate the run could not execute is still judged, from the
     documentation the run already read, on a scale that says so. It ranks below
     everything that was measured — evidence outranks a reading of a docs site —
     but below is not absent, and an unreachable product is no longer a row of
     seven blanks nothing can be compared against.

     Requirement fit partitions within a tier, never across it: for a measured
     candidate the requirement flag is a documentation opinion, and an opinion
     does not get to demote a number the sandbox actually produced. */
  const tierRank = (row) => (evidenceTier(row, isAssessment) === "measured" ? 2 : 1);
  const score = (row) =>
    isFiniteMetric(row[primaryKey]) ? row[primaryKey] : row.research_score;
  const requirementRank = (row) =>
    (isAssessment || tierRank(row) === 1 ? meetsRequirements(row) : 1);
  const valid = rows
    .filter((row) => evidenceTier(row, isAssessment) !== "none")
    .sort((a, b) =>
      tierRank(b) - tierRank(a) ||
      requirementRank(b) - requirementRank(a) ||
      score(b) - score(a) ||
      a.name.localeCompare(b.name));
  const ranks = new Map(valid.map((row, index) => [row.name, index + 1]));
  return rows.map((row) => ({
    ...row,
    canonicalRank: ranks.get(row.name) || null,
    isWinner: ranks.get(row.name) === 1,
  }));
}

/**
 * The one line a decision needs: which candidate ranked first, by how much,
 * and over how many ranked candidates. Returns null when nothing is ranked.
 *
 * `components` comes from `componentRows` and is separate on purpose: the build
 * path is decided from libraries that are deliberately absent from `rows`, so
 * looking for them in the ranking would find nothing and the path would
 * silently disappear.
 */
export function buildDecision(rows, isAssessment = false, components = []) {
  const ranked = (rows || []).filter((row) => row?.canonicalRank);
  const winner = ranked.find((row) => row.canonicalRank === 1) || null;
  if (!winner) return null;
  const runnerUp = ranked.find((row) => row.canonicalRank === 2) || null;
  /* The verdict is stated on the evidence the winner actually has. When nothing
     could be executed, the headline is a documentation score and says so; it is
     never dressed up in the metric column of a benchmark that never ran. */
  const evidence = evidenceTier(winner, isAssessment);
  const key = evidence === "research" ? "research_score" : primaryMetricKey(isAssessment);
  // Like against like only: a measured winner has no margin over a candidate
  // scored from documentation, because the two are not on one scale.
  const comparable = runnerUp && evidenceTier(runnerUp, isAssessment) === evidence;
  const margin = comparable && isFiniteMetric(winner[key]) && isFiniteMetric(runnerUp[key])
    ? winner[key] - runnerUp[key]
    : null;

  /* A ranking is not a recommendation.
     Every candidate in one real run was rated not implementable — each reason
     naming the same unmet hard requirement — and the console still crowned the
     highest scorer as "Recommendation: 49/100, ranked first of 5". Relative
     order among failures is not an endorsement of the least-bad one. */
  const products = [...ranked].sort((a, b) => a.canonicalRank - b.canonicalRank);
  const viableComponents = (components || []).filter((row) => row?.implementable === true);
  const topProduct = products[0] || winner;
  const unmet = isAssessment && topProduct?.implementable === false;
  return {
    key,
    isAssessment,
    evidence,
    winner,
    runnerUp,
    margin,
    rankedCount: ranked.length,
    measuredCount: ranked.filter((row) => evidenceTier(row, isAssessment) === "measured").length,
    researchCount: ranked.filter((row) => evidenceTier(row, isAssessment) === "research").length,
    unmet,
    failedCount: products.filter((row) => row.implementable === false).length,
    // Every product failed but a component is documented well enough to build
    // against: the evidence supports building rather than buying, and saying so
    // beats crowning a product that cannot do the job.
    buildPath: unmet && products.length > 0 && viableComponents.length > 0
      ? viableComponents
      : [],
  };
}

// Backend vocabulary, mapped to the sentence the product uses. An unrecognised
// value is rendered as the backend sent it rather than replaced by a guess.
//
// The label says what happened to the candidate, never which service did it.
// Naming the sandbox vendor told the reader nothing about the evidence and
// leaked an implementation detail into a verdict they may forward onwards.
const BASIS_LABELS = {
  verified_in_daytona: "Verified by execution",
  daytona_verified: "Verified by execution",
  daytona: "Verified by execution",
  sandbox: "Verified by execution",
  sandbox_execution: "Verified by execution",
  documentation: "Compared from documentation",
  documentation_only: "Compared from documentation",
  documentation_evidence: "Compared from documentation",
  comparison_only: "Compared from documentation",
  compared_from_documentation: "Compared from documentation",
  docs: "Compared from documentation",
};

function declaredBasis(value) {
  const raw = String(value ?? "").trim();
  if (!raw) return "";
  return BASIS_LABELS[raw.toLowerCase().replace(/[\s-]+/g, "_")] || raw;
}

/**
 * How the run reached its numbers, taken from the backend only. Reads an
 * explicit execution_mode/assessment_basis first, then the per-candidate
 * daytona_triggered flag the assessment path already reports. Returns null when
 * the backend has said nothing, so the UI claims nothing.
 */
export function executionBasisLabel(metrics, explicit) {
  const fromArtifact = declaredBasis(explicit);
  if (fromArtifact) return fromArtifact;

  const rows = Object.values(metrics || {}).filter((row) => row && typeof row === "object");
  for (const row of rows) {
    const fromRow = declaredBasis(row.assessment_basis || row.execution_mode);
    if (fromRow) return fromRow;
  }

  const flagged = rows.filter((row) => typeof row.daytona_triggered === "boolean");
  if (flagged.length === 0) return null;
  if (flagged.every((row) => row.daytona_triggered)) return "Verified by execution";
  if (flagged.some((row) => row.daytona_triggered)) return "Partly verified by execution";
  return "Compared from documentation";
}

export function sortResultRows(rows, key, direction = "desc") {
  return [...rows].sort((a, b) => {
    const av = a[key];
    const bv = b[key];
    const aMissing = av === null || av === undefined || av === "" ||
      (typeof av === "number" && !Number.isFinite(av));
    const bMissing = bv === null || bv === undefined || bv === "" ||
      (typeof bv === "number" && !Number.isFinite(bv));
    if (aMissing !== bMissing) return aMissing ? 1 : -1;
    if (aMissing && bMissing) return a.name.localeCompare(b.name);
    const aValue = typeof av === "boolean" ? Number(av) : av;
    const bValue = typeof bv === "boolean" ? Number(bv) : bv;
    const comparison = typeof aValue === "number" && typeof bValue === "number"
      ? aValue - bValue
      : String(aValue).localeCompare(String(bValue), undefined, { numeric: true });
    return (direction === "asc" ? comparison : -comparison) || a.name.localeCompare(b.name);
  });
}
