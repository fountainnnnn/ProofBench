// ProofBench runs are real-only. Evidence status decides what the UI is
// allowed to present as a result:
//
//   measured    immutable persisted evidence from a genuine execution
//   synthetic   a run persisted by an earlier demo-capable version; still
//               readable, always labelled historical, never shown as real
//   pending     a run that has not produced immutable evidence yet
//   unverified  metrics without a trustworthy provenance marker
//
// pending and unverified must never render metrics. Nothing here may infer
// "measured" from anything other than an explicit backend marker.
const STATUSES = new Set(["measured", "synthetic", "pending", "unverified"]);
const LEGACY_MODES = new Set(["demo", "real"]);

export function hasAuthoritativeProvenance(payload) {
  return Boolean(payload && typeof payload === "object" && (
    payload.provenance !== undefined ||
    LEGACY_MODES.has(payload.mode) ||
    typeof payload.demo_mode === "boolean"
  ));
}

export function authoritativeProvenance(payload = {}, fallback = {}) {
  const declared = typeof payload?.provenance === "object" && payload?.provenance !== null
    ? payload.provenance
    : { provenance: payload?.provenance };
  const marker = String(declared.provenance || declared.status || "").toLowerCase();

  let status;
  if (STATUSES.has(marker)) {
    status = marker;
  } else if (declared.mode === "demo" || payload?.mode === "demo" || payload?.demo_mode === true) {
    // Legacy payloads that predate the provenance marker. Only "demo" is read
    // this way: it can only ever downgrade a run to synthetic, so an old
    // persisted demo run stays readable. A legacy mode:"real" is deliberately
    // NOT accepted here — it would let unmarked data masquerade as measured.
    status = "synthetic";
  } else if (STATUSES.has(fallback.status)) {
    status = fallback.status;
  } else {
    // Absence of evidence is never evidence. A bare mode:"real" does not prove
    // a measured execution happened, so it stays pending.
    status = "pending";
  }

  return {
    status,
    // Derived compatibility view for callers that still branch on mode. It is
    // deliberately null unless the evidence is conclusive, so nothing can read
    // "real" out of a pending or unverified run.
    mode: status === "measured" ? "real" : status === "synthetic" ? "demo" : null,
    datasetKind: declared.datasetKind || fallback.datasetKind || "unknown",
    source: marker || declared.source || fallback.source || "session",
  };
}

/** Immutable persisted evidence from a genuine execution. */
export function isMeasured(provenance) {
  return provenance?.status === "measured";
}

/** A run kept from an earlier demo-capable version. Read-only history. */
export function isHistoricalSynthetic(provenance) {
  return provenance?.status === "synthetic";
}

/**
 * Whether the backend has stated what this artifact is. "measured" and
 * "synthetic" are conclusive claims; "pending" and "unverified" are the absence
 * of one. Only a conclusive claim can contradict another conclusive claim.
 */
export function isConclusive(provenance) {
  return isMeasured(provenance) || isHistoricalSynthetic(provenance);
}

/** Whether metrics may be shown at all. pending/unverified suppress them. */
export function canRenderMetrics(provenance) {
  return isConclusive(provenance);
}

/** Short label for the evidence badge, or null when results are real. */
export function provenanceLabel(provenance) {
  switch (provenance?.status) {
    case "synthetic": return "Historical synthetic run";
    case "pending": return "Awaiting verified results";
    case "unverified": return "Unverified. Results withheld";
    default: return null;
  }
}

/**
 * Cross-artifact consistency, not an evidence gate. A restored session usually
 * carries an unmarked specification beside an authoritative measured result:
 * that is a missing marker on the specification, not a contradiction, so it
 * must not present the run as unavailable. Two conclusive but incompatible
 * claims still block. This does not loosen anything: metrics and reports pass
 * through canRenderMetrics on their own provenance regardless of what is
 * returned here, so pending or unverified results stay withheld.
 */
export function provenanceMatches(left, right) {
  if (!left || !right) return true;
  if (!isConclusive(left) || !isConclusive(right)) return true;
  return left.status === right.status &&
    (left.datasetKind === "unknown" || right.datasetKind === "unknown" ||
      left.datasetKind === right.datasetKind);
}
