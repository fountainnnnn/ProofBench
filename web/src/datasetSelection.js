import { safeVisibleText } from "./displaySafety.js";

export function resolveDatasetSelection(records, requestedId) {
  const id = String(requestedId || "").trim();
  if (!id) return { dataset: null, error: "" };
  const dataset = (records || []).find((item) => item?.id === id || item?.dataset_id === id);
  if (dataset) return { dataset, error: "" };
  return {
    dataset: null,
    error: `Dataset ${safeVisibleText(id)} is unavailable or you do not have access.`,
  };
}
