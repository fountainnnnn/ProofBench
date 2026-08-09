import { useEffect, useState } from "react";
import { datasetImageUrl, getDatasetPreview } from "../api.js";
import { safeVisibleText } from "../displaySafety.js";

/* What a dataset IS, before anything runs against it: its kind, the typed
   schema a run will be scored on, a few real document images, and the first
   ground-truth rows. One component serves both the post-generation approval
   view and the library's per-dataset preview, so the user always reads the
   same account of the same data. */

const KIND_LABELS = {
  synthetic: "Sample",
  generated: "AI-generated",
  upload: "Uploaded",
};

const THUMBS = 3;
const TYPE_TONES = {
  date: "text-[var(--warn)] bg-[color-mix(in_oklab,var(--warn)_12%,transparent)]",
  currency: "text-[var(--ok)] bg-[color-mix(in_oklab,var(--ok)_12%,transparent)]",
  number: "text-[var(--accent)] bg-[var(--accent-tint)]",
  text: "text-[var(--ink-2)] bg-[var(--surface-2)]",
};

export function SchemaChips({ schema }) {
  if (!Array.isArray(schema) || schema.length === 0) return null;
  return (
    <ul className="flex flex-wrap gap-1.5" aria-label="Extraction schema">
      {schema.map((field) => (
        <li
          key={field.name}
          className={`inline-flex items-baseline gap-1 rounded-full px-2.5 py-0.5 ${TYPE_TONES[field.type] || TYPE_TONES.text}`}
        >
          <span className="pb-mono text-[11px] font-medium">{safeVisibleText(field.name)}</span>
          {field.type !== "text" && (
            <span className="text-[10px] opacity-70">{safeVisibleText(field.type)}</span>
          )}
        </li>
      ))}
    </ul>
  );
}

export default function DatasetPreview({ preview, datasetId, compact = false }) {
  const [loaded, setLoaded] = useState(preview || null);
  const [error, setError] = useState(null);
  const id = preview?.dataset_id || datasetId;

  useEffect(() => {
    if (preview) {
      setLoaded(preview);
      return undefined;
    }
    if (!datasetId) return undefined;
    let cancelled = false;
    getDatasetPreview(datasetId)
      .then((value) => { if (!cancelled) setLoaded(value); })
      .catch((exc) => { if (!cancelled) setError(exc.message); });
    return () => { cancelled = true; };
  }, [preview, datasetId]);

  if (error) {
    return (
      <p className="text-[12px] text-[var(--danger)]" role="alert">
        Preview unavailable: {safeVisibleText(error)}
      </p>
    );
  }
  if (!loaded) {
    return <p className="text-[12px] text-[var(--ink-3)]">Loading preview…</p>;
  }

  const kind = KIND_LABELS[loaded.kind] || "Dataset";
  const columns = (loaded.schema || []).map((field) => field.name);
  const rows = loaded.rows || [];
  const thumbs = (loaded.doc_ids || []).slice(0, THUMBS);

  return (
    <div className="flex flex-col gap-3" data-dataset-preview>
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <span className="rounded-full bg-[var(--accent-tint)] px-2.5 py-0.5 text-[11px] font-medium text-[var(--accent)]">
          {kind}
        </span>
        {loaded.title && (
          <span className="text-[13px] font-medium text-[var(--ink)]">
            {safeVisibleText(loaded.title)}
          </span>
        )}
        <span className="text-[12px] text-[var(--ink-2)]">
          {loaded.image_count || (loaded.doc_ids || []).length} documents
        </span>
      </div>

      {loaded.description && !compact && (
        <p className="max-w-[64ch] text-[12px] leading-relaxed text-[var(--ink-2)]">
          {safeVisibleText(loaded.description)}
        </p>
      )}

      <SchemaChips schema={loaded.schema} />

      {thumbs.length > 0 && (
        <ul className="flex gap-2" aria-label="Sample documents">
          {thumbs.map((docId) => (
            <li key={docId} className="overflow-hidden rounded-[10px] border border-[var(--line)] bg-white">
              <img
                src={datasetImageUrl(id, docId)}
                alt={`Document ${safeVisibleText(docId)}`}
                loading="lazy"
                className="h-28 w-24 object-cover object-top"
              />
            </li>
          ))}
        </ul>
      )}

      {rows.length > 0 && columns.length > 0 && (
        <div className="overflow-x-auto rounded-[10px] border border-[var(--line)]">
          <table className="w-full border-collapse text-left">
            <caption className="sr-only">First ground-truth rows</caption>
            <thead>
              <tr className="border-b border-[var(--line)] bg-[var(--surface-2)]">
                <th scope="col" className="px-2.5 py-1.5 pb-mono text-[10px] font-medium text-[var(--ink-3)]">doc_id</th>
                {columns.map((name) => (
                  <th key={name} scope="col" className="px-2.5 py-1.5 pb-mono text-[10px] font-medium text-[var(--ink-3)]">
                    {safeVisibleText(name)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, compact ? 3 : 6).map((row) => (
                <tr key={row.doc_id} className="border-b border-[var(--line)] last:border-b-0">
                  <td className="px-2.5 py-1.5 pb-mono text-[11px] text-[var(--ink-3)]">
                    {safeVisibleText(row.doc_id)}
                  </td>
                  {columns.map((name) => (
                    <td key={name} className="max-w-44 truncate px-2.5 py-1.5 text-[11px] text-[var(--ink-2)]">
                      {safeVisibleText(String(row[name] ?? ""))}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
