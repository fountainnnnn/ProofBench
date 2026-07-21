import { describe, expect, it } from "vitest";
import {
  authoritativeProvenance,
  canRenderMetrics,
  hasAuthoritativeProvenance,
  isConclusive,
  isHistoricalSynthetic,
  isMeasured,
  provenanceLabel,
  provenanceMatches,
} from "./provenance.js";

describe("authoritative provenance", () => {
  it("uses the backend marker instead of client fallback heuristics", () => {
    expect(authoritativeProvenance(
      { provenance: "measured", tool: "simulated_demo_tool", metrics: { alpha: { is_demo: true } } },
      { status: "synthetic", datasetKind: "synthetic" }
    )).toMatchObject({ status: "measured", datasetKind: "synthetic", source: "measured" });
    expect(hasAuthoritativeProvenance({ provenance: "measured" })).toBe(true);
    expect(hasAuthoritativeProvenance({ tool: "simulated_demo_tool", metrics: { alpha: { is_demo: true } } })).toBe(false);
  });

  it("never infers measured evidence from an unmarked payload", () => {
    expect(authoritativeProvenance({}).status).toBe("pending");
    expect(authoritativeProvenance({ mode: "real" }).status).toBe("pending");
    expect(authoritativeProvenance({ metrics: { alpha: { exact_accuracy: 1 } } }).status).toBe("pending");
  });

  it("suppresses metrics for pending and unverified evidence", () => {
    for (const status of ["pending", "unverified"]) {
      const provenance = authoritativeProvenance({ provenance: status });
      expect(provenance.status).toBe(status);
      expect(isMeasured(provenance)).toBe(false);
      expect(canRenderMetrics(provenance)).toBe(false);
      expect(provenanceLabel(provenance)).toBeTruthy();
    }
  });

  it("keeps legacy demo runs readable but visibly synthetic", () => {
    for (const payload of [{ provenance: "synthetic" }, { mode: "demo" }, { demo_mode: true }]) {
      const provenance = authoritativeProvenance(payload);
      expect(provenance.status).toBe("synthetic");
      expect(isHistoricalSynthetic(provenance)).toBe(true);
      expect(isMeasured(provenance)).toBe(false);
      // Still readable, just never presented as a real result.
      expect(canRenderMetrics(provenance)).toBe(true);
      expect(provenanceLabel(provenance)).toBe("Historical synthetic run");
    }
  });

  it("shows no badge for measured evidence", () => {
    expect(provenanceLabel(authoritativeProvenance({ provenance: "measured" }))).toBeNull();
  });

  it("detects a restored specification/results provenance mismatch", () => {
    const spec = authoritativeProvenance({ provenance: "synthetic" }, { datasetKind: "synthetic" });
    const results = authoritativeProvenance({ provenance: "measured" }, { datasetKind: "upload" });
    expect(provenanceMatches(spec, results)).toBe(false);
  });
});

describe("restored provenance consistency", () => {
  // A restored session usually replays a specification artifact that carries no
  // provenance marker of its own, beside an immutable result that does. The
  // missing marker is not a contradiction and must not present a genuinely
  // measured run as unavailable.
  const measured = authoritativeProvenance({ provenance: "measured" }, { datasetKind: "synthetic" });

  it("does not fabricate a mismatch from an unmarked specification", () => {
    for (const unmarked of [{}, { mode: "real" }, { provenance: "pending" }, { provenance: "unverified" }]) {
      const spec = authoritativeProvenance(unmarked, { datasetKind: "synthetic" });
      expect(isConclusive(spec)).toBe(false);
      expect(provenanceMatches(spec, measured)).toBe(true);
      expect(provenanceMatches(measured, spec)).toBe(true);
    }
  });

  it("keeps blocking when both sides state conclusive and incompatible evidence", () => {
    const synthetic = authoritativeProvenance({ provenance: "synthetic" }, { datasetKind: "synthetic" });
    expect(provenanceMatches(synthetic, measured)).toBe(false);
    expect(provenanceMatches(
      authoritativeProvenance({ provenance: "measured" }, { datasetKind: "upload" }),
      authoritativeProvenance({ provenance: "measured" }, { datasetKind: "synthetic" })
    )).toBe(false);
  });

  it("still withholds metrics for inconclusive results regardless of the match check", () => {
    for (const status of ["pending", "unverified"]) {
      const results = authoritativeProvenance({ provenance: status });
      // The match check no longer objects, and the evidence gate still does.
      expect(provenanceMatches(measured, results)).toBe(true);
      expect(canRenderMetrics(results)).toBe(false);
    }
  });
});
