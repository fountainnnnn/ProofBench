import { describe, expect, it } from "vitest";
import { resolveDatasetSelection } from "./datasetSelection.js";

describe("dataset query selection", () => {
  it("keeps an unknown dataset unresolved instead of fabricating a record", () => {
    const result = resolveDatasetSelection([{ id: "known", kind: "upload" }], "missing");
    expect(result.dataset).toBeNull();
    expect(result.error).toContain("missing");
  });

  it("returns the authoritative catalog record", () => {
    const record = { id: "known", kind: "synthetic" };
    expect(resolveDatasetSelection([record], "known")).toEqual({ dataset: record, error: "" });
  });
});
