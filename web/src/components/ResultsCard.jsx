import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { getReportPdfUrl } from "../api.js";

const OCR_COLUMNS = [
  { key: "exact_accuracy", label: "Exact acc.", pct: true, higher: true },
  { key: "field_f1", label: "F1", pct: true, higher: true },
  { key: "cer", label: "CER", pct: true, higher: false },
  { key: "mean_latency_s", label: "Latency (s)", higher: false },
  { key: "failure_rate", label: "Failure", pct: true, higher: false },
  { key: "cost_per_1k_docs", label: "Cost/1k", higher: false },
  { key: "setup_complexity", label: "Setup", higher: false },
];

const ASSESSMENT_COLUMNS = [
  { key: "rating", label: "Rating", higher: true, score: true, bar: true },
  { key: "implementable", label: "Implementable", higher: true, boolean: true },
  { key: "documentation_quality", label: "Docs", higher: true, score: true },
  { key: "integration_feasibility", label: "Feasibility", higher: true, score: true },
  { key: "auth_clarity", label: "Auth", higher: true, score: true },
  { key: "setup_complexity", label: "Setup", higher: false },
  { key: "daytona_triggered", label: "Daytona", higher: true, daytona: true },
];

function fmt(col, v) {
  if (v === null || v === undefined) return "-";
  if (col.boolean) return v ? "Yes" : "No";
  if (col.daytona) return v ? "Used" : "Skipped";
  if (col.score) return `${Math.round(Number(v) || 0)}/100`;
  if (col.pct) return `${(v * 100).toFixed(1)}%`;
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(2);
  return String(v);
}

function Bar({ value, max = 1 }) {
  const pct = Math.max(0, Math.min(1, (value || 0) / max)) * 100;
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-[var(--surface-2)]">
      <div
        className="h-full rounded-full bg-[var(--accent)]"
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

function SkeletonRows({ columns }) {
  return (
    <tbody>
      {[0, 1, 2].map((r) => (
        <tr key={r} className="border-b border-[var(--border)]">
          <td className="px-3 py-2.5">
            <div className="h-3 w-4 animate-pulse rounded bg-[var(--surface-2)]" />
          </td>
          <td className="px-3 py-2.5">
            <div className="h-3 w-24 animate-pulse rounded bg-[var(--surface-2)]" />
          </td>
          {columns.map((c) => (
            <td key={c.key} className="px-3 py-2.5">
              <div className="ml-auto h-3 w-12 animate-pulse rounded bg-[var(--surface-2)]" />
            </td>
          ))}
        </tr>
      ))}
    </tbody>
  );
}

export default function ResultsCard({ metrics, report, runId }) {
  const [sortKey, setSortKey] = useState("exact_accuracy");
  const [isPdfPreviewOpen, setIsPdfPreviewOpen] = useState(false);
  const isAssessment = Object.values(metrics || {}).some((row) => row?.rating !== undefined);
  const columns = isAssessment ? ASSESSMENT_COLUMNS : OCR_COLUMNS;
  const activeSortKey = columns.some((column) => column.key === sortKey)
    ? sortKey
    : columns[0].key;

  const rows = useMemo(() => {
    const entries = Object.entries(metrics || {}).map(([name, m]) => ({ name, ...m }));
    const col = columns.find((c) => c.key === activeSortKey) || columns[0];
    entries.sort((a, b) => {
      const av = a[activeSortKey] ?? 0;
      const bv = b[activeSortKey] ?? 0;
      return col.higher ? bv - av : av - bv;
    });
    return entries;
  }, [metrics, columns, activeSortKey]);

  const downloadMarkdown = () => {
    const md = report?.markdown || "";
    const blob = new Blob([md], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "proofbench_report.md";
    a.click();
    URL.revokeObjectURL(url);
  };

  useEffect(() => {
    if (!isPdfPreviewOpen) return undefined;

    const onKeyDown = (event) => {
      if (event.key === "Escape") setIsPdfPreviewOpen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [isPdfPreviewOpen]);

  const pdfPreviewUrl = runId ? getReportPdfUrl(runId) : "";
  const pdfDownloadUrl = runId ? getReportPdfUrl(runId, true) : "";

  const sortCol = columns.find((c) => c.key === activeSortKey) || columns[0];
  const pdfModal = isPdfPreviewOpen
    ? createPortal(
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-[color:color-mix(in_oklab,var(--text)_35%,transparent)] p-4"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setIsPdfPreviewOpen(false);
          }}
        >
          <section
            role="dialog"
            aria-modal="true"
            aria-labelledby="pdf-preview-title"
            className="flex h-[min(48rem,calc(100dvh-2rem))] w-full max-w-5xl flex-col overflow-hidden rounded-[10px] border border-[var(--border)] bg-[var(--surface)] shadow-lift"
          >
            <header className="flex items-center gap-3 border-b border-[var(--border)] px-4 py-3">
              <div>
                <h2 id="pdf-preview-title" className="text-[16px] font-semibold text-[var(--text)]">
                  Report PDF
                </h2>
                <p className="mt-0.5 text-[12px] text-[var(--text-2)]">
                  Preview the completed report before downloading it.
                </p>
              </div>
              <div className="ml-auto flex items-center gap-2">
                <a
                  href={pdfDownloadUrl}
                  className="inline-flex h-9 items-center justify-center rounded-md bg-[var(--accent)] px-3 text-[13px] font-medium text-[var(--surface)] transition-colors hover:bg-[var(--accent-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:color-mix(in_oklab,var(--accent)_40%,transparent)]"
                >
                  Download PDF
                </a>
                <button
                  type="button"
                  onClick={() => setIsPdfPreviewOpen(false)}
                  className="inline-flex h-9 items-center justify-center rounded-md border border-[var(--border)] px-3 text-[13px] font-medium text-[var(--text)] transition-colors hover:border-[var(--border-strong)] hover:bg-[var(--surface-2)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:color-mix(in_oklab,var(--accent)_40%,transparent)]"
                >
                  Close
                </button>
              </div>
            </header>
            <iframe
              title="ProofBench report PDF preview"
              src={pdfPreviewUrl}
              className="min-h-0 w-full flex-1 bg-[var(--surface-2)]"
            />
          </section>
        </div>,
        document.body
      )
    : null;

  return (
    <>
      <div className="pb-card pb-card-hover pb-hover-lift p-4">
      <div className="mb-3 flex items-center justify-between">
        <span className="text-[16px] font-semibold text-[var(--text)]">Results</span>
        {report?.markdown && (
          <div className="flex items-center gap-2">
            {runId && (
              <button
                onClick={() => setIsPdfPreviewOpen(true)}
                className="h-9 rounded-md bg-[var(--accent)] px-3 text-[13px] font-medium text-[var(--surface)] transition-colors hover:bg-[var(--accent-hover)]"
              >
                Preview PDF
              </button>
            )}
            <button
              onClick={downloadMarkdown}
            className="pb-hover-lift h-9 rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 text-[13px] font-medium text-[var(--text)] transition-colors hover:border-[var(--border-strong)] hover:bg-[var(--surface-2)]"
            >
              Markdown
            </button>
          </div>
        )}
      </div>

      <div className="mb-4 overflow-x-auto rounded-md border border-[var(--border)]">
        <table className="w-full text-[13px]">
          <thead>
            <tr className="bg-[var(--surface-2)]">
              <th className="px-3 py-2 text-left text-[12px] font-semibold text-[var(--text-2)]">
                #
              </th>
              <th className="px-3 py-2 text-left text-[12px] font-semibold text-[var(--text-2)]">
                Candidate
              </th>
              {columns.map((c) => (
                <th
                  key={c.key}
                  onClick={() => setSortKey(c.key)}
                  className={`cursor-pointer select-none px-3 py-2 text-right text-[12px] font-semibold transition-colors hover:text-[var(--text)] ${
                    activeSortKey === c.key ? "text-[var(--accent)]" : "text-[var(--text-2)]"
                  }`}
                  title="Sort"
                >
                  {c.label}
                  {activeSortKey === c.key && (
                    <span className="ml-1">{sortCol.higher ? "v" : "^"}</span>
                  )}
                </th>
              ))}
            </tr>
          </thead>
          {rows.length === 0 ? (
            <SkeletonRows columns={columns} />
          ) : (
            <tbody>
              {rows.map((r, i) => (
                <tr
                  key={r.name}
                  className={`border-b border-[var(--border)] text-[var(--text-2)] transition-colors last:border-b-0 hover:bg-[color-mix(in_oklab,var(--accent-soft)_50%,transparent)] ${
                    i === 0 ? "bg-[var(--accent-soft)]" : ""
                  }`}
                >
                  <td className="px-3 py-2.5 font-mono text-[12px] text-[var(--text-3)]">
                    {i + 1}
                  </td>
                  <td className="px-3 py-2.5 font-medium text-[var(--text)]">
                    {r.name}
                  </td>
                  {columns.map((c) => (
                    <td
                      key={c.key}
                      className="px-3 py-2.5 text-right font-mono text-[12px] tabular-nums"
                    >
                      {c.bar || c.key === "exact_accuracy" || c.key === "field_f1" ? (
                        <div className="flex flex-col items-end gap-1">
                          <span>{fmt(c, r[c.key])}</span>
                          <div className="w-20">
                            <Bar value={r[c.key]} max={c.score ? 100 : 1} />
                          </div>
                        </div>
                      ) : (
                        fmt(c, r[c.key])
                      )}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          )}
        </table>
      </div>

      {rows.length > 0 && !report?.markdown && (
        <div className="mb-3 flex items-center gap-2 text-[12px] text-[var(--text-3)]">
          <span className="h-2 w-2 animate-pulse rounded-full bg-[var(--info)]" />
          Preparing report
        </div>
      )}

      {report?.citations && report.citations.length > 0 && (
        <div className="mt-3 border-t border-[var(--border)] pt-3">
          <div className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-[var(--text-3)]">
            Citations
          </div>
          <ul className="flex flex-col gap-1">
            {report.citations.map((c, i) => (
              <li key={i}>
                <a
                  href={c.url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-[12px] text-[var(--accent)] hover:underline"
                >
                  {c.title || c.url}
                </a>
              </li>
            ))}
          </ul>
        </div>
      )}

      </div>
      {pdfModal}
    </>
  );
}
