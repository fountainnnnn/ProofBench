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
  return Object.values(metrics).some((row) => isFiniteMetric(row?.[primary]))
    ? "ready"
    : "unavailable";
}

export function buildCanonicalRows(metrics, isAssessment = false) {
  const primaryKey = primaryMetricKey(isAssessment);
  const rows = Object.entries(metrics || {}).map(([name, values]) => ({ name, ...values }));
  const valid = rows
    .filter((row) => isFiniteMetric(row[primaryKey]))
    .sort((a, b) => b[primaryKey] - a[primaryKey] || a.name.localeCompare(b.name));
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
 */
export function buildDecision(rows, isAssessment = false) {
  const key = primaryMetricKey(isAssessment);
  const ranked = (rows || []).filter((row) => row?.canonicalRank);
  const winner = ranked.find((row) => row.canonicalRank === 1) || null;
  if (!winner) return null;
  const runnerUp = ranked.find((row) => row.canonicalRank === 2) || null;
  const margin = runnerUp && isFiniteMetric(winner[key]) && isFiniteMetric(runnerUp[key])
    ? winner[key] - runnerUp[key]
    : null;
  return { key, isAssessment, winner, runnerUp, margin, rankedCount: ranked.length };
}

// Backend vocabulary, mapped to the sentence the product uses. An unrecognised
// value is rendered as the backend sent it rather than replaced by a guess.
const BASIS_LABELS = {
  verified_in_daytona: "Verified in Daytona",
  daytona_verified: "Verified in Daytona",
  daytona: "Verified in Daytona",
  sandbox: "Verified in Daytona",
  sandbox_execution: "Verified in Daytona",
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
  if (flagged.every((row) => row.daytona_triggered)) return "Verified in Daytona";
  if (flagged.some((row) => row.daytona_triggered)) return "Partly verified in Daytona";
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
