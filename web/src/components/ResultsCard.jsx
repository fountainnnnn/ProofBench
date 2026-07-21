import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { prepareReportPdf } from "../api.js";
import { BasisTag, BTN_PRIMARY, BTN_SECONDARY, MARKDOWN_HEADINGS_IN_REPORT, PANEL } from "./ui.jsx";
import { safeVisibleText, sanitizeForDisplay } from "../displaySafety.js";
import { safeHttpUrl } from "../linkSafety.js";
import {
  buildCanonicalRows,
  buildDecision,
  classifyResultsState,
  executionBasisLabel,
  sortResultRows,
} from "../resultsModel.js";

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
  { key: "daytona_triggered", label: "Execution", higher: true, daytona: true },
];

function fmt(col, v) {
  if (v === null || v === undefined || (typeof v === "number" && !Number.isFinite(v))) return "Unavailable";
  if (col.boolean) return v ? "Yes" : "No";
  // Never let a documentation-only candidate read as if it had been executed.
  if (col.daytona) return v ? "Verified" : "Docs only";
  if (col.score) return `${Math.round(Number(v) || 0)}/100`;
  if (col.pct) return `${(v * 100).toFixed(1)}%`;
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(2);
  return String(v);
}

function Bar({ value, max = 1 }) {
  const finiteValue = typeof value === "number" && Number.isFinite(value) ? value : 0;
  const pct = Math.max(0, Math.min(1, finiteValue / max)) * 100;
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-[var(--surface-2)]">
      <div className="h-full rounded-full bg-[var(--accent)]" style={{ width: `${pct}%` }} />
    </div>
  );
}

function SkeletonRows({ columns }) {
  return (
    <tbody>
      {[0, 1, 2].map((r) => (
        <tr key={r}>
          <td className="px-3 py-3">
            <div className="pb-skeleton h-3 w-4" />
          </td>
          <td className="px-3 py-3">
            <div className="pb-skeleton h-3 w-24" />
          </td>
          {columns.map((c) => (
            <td key={c.key} className="px-3 py-3">
              <div className="pb-skeleton ml-auto h-3 w-12" />
            </td>
          ))}
        </tr>
      ))}
    </tbody>
  );
}

function SafeMarkdownLink({ href, children }) {
  const safeHref = safeHttpUrl(href);
  return safeHref ? (
    <a href={safeHref} target="_blank" rel="noreferrer">{children}</a>
  ) : <span>{children}</span>;
}

/* The verdict. First thing on the card, and the only place a winner is named. */
function DecisionSummary({ decision, basis, evidence, documentCount }) {
  const key = decision.key;
  const column = (decision.isAssessment ? ASSESSMENT_COLUMNS : OCR_COLUMNS)
    .find((c) => c.key === key) || { key };
  const winnerScore = fmt(column, decision.winner[key]);
  // A tie is reported as a tie. Saying "0 ahead" would dress a dead heat up as
  // a win the numbers do not support.
  const roundedMargin = column.pct
    ? Number((Math.abs(decision.margin ?? 0) * 100).toFixed(1))
    : Math.abs(Math.round(decision.margin ?? 0));
  const marginText = decision.margin === null || decision.runnerUp === null
    ? null
    : roundedMargin === 0
      ? `Level with ${safeVisibleText(decision.runnerUp.name)} on this metric`
      : column.pct
        ? `${roundedMargin.toFixed(1)} points ahead of ${safeVisibleText(decision.runnerUp.name)}`
        : `${roundedMargin} ahead of ${safeVisibleText(decision.runnerUp.name)}`;

  return (
    <div className="pb-verdict-enter rounded-[20px] bg-[var(--hero-ink)] p-6 shadow-[var(--shadow-card)]">
      <p className="text-[11px] font-semibold uppercase tracking-[0.06em] text-[var(--hero-ink-2)]">
        Recommendation
      </p>
      <div className="mt-2 flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <span className="pb-display pb-contain text-[34px] leading-tight text-[var(--hero-text)] sm:text-[38px]">
          {safeVisibleText(decision.winner.name)}
        </span>
        <span className="pb-mono text-[16px] font-medium text-[var(--hero-text)]">
          {column.label ? `${column.label} ${winnerScore}` : winnerScore}
        </span>
      </div>
      <p className="mt-2 max-w-[70ch] text-[14px] leading-relaxed text-[var(--hero-ink-2)]">
        {marginText
          ? `${marginText}. Ranked first of ${decision.rankedCount}.`
          : `Ranked first of ${decision.rankedCount}. No comparable runner up.`}
      </p>
      <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2">
        <BasisTag basis={basis} dark />
        {evidence && (
          <span className="text-[12px] text-[var(--hero-ink-2)]">
            Evidence <span className="text-[var(--hero-text)]">{safeVisibleText(evidence)}</span>
          </span>
        )}
        {documentCount && (
          <span className="text-[12px] text-[var(--hero-ink-2)]">
            Documents <span className="pb-mono text-[var(--hero-text)]">{safeVisibleText(documentCount)}</span>
          </span>
        )}
      </div>
    </div>
  );
}

export default function ResultsCard({
  metrics,
  report,
  runId,
  simulated,
  phase,
  running = false,
  executionMode = "",
  headingId,
}) {
  const [sortKey, setSortKey] = useState("exact_accuracy");
  const [sortDirection, setSortDirection] = useState("desc");
  const [isPdfPreviewOpen, setIsPdfPreviewOpen] = useState(false);
  const [pdfPreviewUrl, setPdfPreviewUrl] = useState("");
  const [pdfBusy, setPdfBusy] = useState(false);
  const [pdfError, setPdfError] = useState("");
  const previewButtonRef = useRef(null);
  const dialogRef = useRef(null);
  const safeMetrics = useMemo(() => sanitizeForDisplay(metrics || null), [metrics]);
  const safeReport = useMemo(() => sanitizeForDisplay(report || null), [report]);
  const resultState = classifyResultsState(metrics, phase, running);
  const isAssessment = Object.values(safeMetrics || {}).some((row) => row?.rating !== undefined);
  const columns = isAssessment ? ASSESSMENT_COLUMNS : OCR_COLUMNS;
  const activeSortKey = columns.some((column) => column.key === sortKey)
    ? sortKey
    : columns[0].key;

  const canonicalRows = useMemo(
    () => buildCanonicalRows(safeMetrics, isAssessment),
    [safeMetrics, isAssessment]
  );
  const rows = useMemo(
    () => sortResultRows(canonicalRows, activeSortKey, sortDirection),
    [canonicalRows, activeSortKey, sortDirection]
  );
  const decision = useMemo(
    () => (resultState === "ready" ? buildDecision(canonicalRows, isAssessment) : null),
    [canonicalRows, isAssessment, resultState]
  );
  const basis = useMemo(
    () => executionBasisLabel(safeMetrics, executionMode),
    [safeMetrics, executionMode]
  );
  const documentCount = useMemo(() => {
    const counts = canonicalRows
      .map((row) => row.n_docs)
      .filter((value) => typeof value === "number" && Number.isFinite(value) && value > 0);
    return counts.length > 0 ? String(Math.max(...counts)) : "";
  }, [canonicalRows]);

  const changeSort = (column) => {
    if (activeSortKey === column.key) {
      setSortDirection((direction) => direction === "asc" ? "desc" : "asc");
      return;
    }
    setSortKey(column.key);
    setSortDirection(column.higher ? "desc" : "asc");
  };

  const openPdfPreview = async () => {
    if (!runId || pdfBusy) return;
    setPdfBusy(true);
    setPdfError("");
    try {
      const url = await prepareReportPdf(runId);
      setPdfPreviewUrl(url);
      setIsPdfPreviewOpen(true);
    } catch {
      setPdfError("PDF access could not be refreshed. Check the server and retry.");
    } finally {
      setPdfBusy(false);
    }
  };

  const downloadPdf = async () => {
    if (!runId || pdfBusy) return;
    setPdfBusy(true);
    setPdfError("");
    try {
      const url = await prepareReportPdf(runId, true);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "";
      anchor.click();
    } catch {
      setPdfError("PDF access could not be refreshed. Check the server and retry.");
    } finally {
      setPdfBusy(false);
    }
  };

  const downloadMarkdown = () => {
    const md = safeVisibleText(safeReport?.markdown || "");
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

    const root = document.getElementById("root");
    const previousAriaHidden = root?.getAttribute("aria-hidden");
    const previousInert = root?.inert || false;
    const focusTimer = window.setTimeout(() => {
      dialogRef.current?.querySelector("button, a[href], iframe")?.focus();
      if (root) {
        root.inert = true;
        root.setAttribute("aria-hidden", "true");
      }
    }, 0);

    const onKeyDown = (event) => {
      if (event.key === "Escape") setIsPdfPreviewOpen(false);
      if (event.key !== "Tab" || !dialogRef.current) return;
      const focusable = [...dialogRef.current.querySelectorAll("a[href], button:not([disabled]), iframe, [tabindex]:not([tabindex='-1'])")];
      if (focusable.length === 0) {
        event.preventDefault();
        dialogRef.current.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      window.clearTimeout(focusTimer);
      document.removeEventListener("keydown", onKeyDown);
      if (root) {
        root.inert = previousInert;
        if (previousAriaHidden === null) root.removeAttribute("aria-hidden");
        else root.setAttribute("aria-hidden", previousAriaHidden);
      }
      previewButtonRef.current?.focus();
    };
  }, [isPdfPreviewOpen]);

  const pdfModal = isPdfPreviewOpen
    ? createPortal(
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-[color:color-mix(in_oklab,var(--ink)_35%,transparent)] p-4"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setIsPdfPreviewOpen(false);
          }}
        >
          <section
            ref={dialogRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby="pdf-preview-title"
            tabIndex={-1}
            className="flex h-[min(48rem,calc(100dvh-1rem))] w-full max-w-canvas flex-col overflow-hidden rounded-[24px] bg-[var(--surface)] shadow-[var(--shadow-lift)]"
          >
            <header className="flex flex-col gap-3 border-b border-[var(--line)] px-4 py-3 sm:flex-row sm:items-center">
              <div>
                <h2 id="pdf-preview-title" className="text-[16px] font-semibold text-[var(--ink)]">
                  Report PDF
                </h2>
                <p className="mt-0.5 text-[12px] text-[var(--ink-2)]">
                  Preview the completed report before downloading it.
                </p>
              </div>
              <div className="flex w-full items-center gap-2 sm:ml-auto sm:w-auto">
                <button type="button" onClick={downloadPdf} disabled={pdfBusy} className={BTN_PRIMARY}>
                  {pdfBusy ? "Preparing" : "Download PDF"}
                </button>
                <button type="button" onClick={() => setIsPdfPreviewOpen(false)} className={BTN_SECONDARY}>
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
      <div className="flex min-w-0 flex-col gap-4">
        {decision && (
          <DecisionSummary
            decision={decision}
            basis={basis}
            evidence={simulated ? "Historical synthetic" : "Measured"}
            documentCount={documentCount}
          />
        )}

        <section className={`${PANEL} min-w-0 overflow-hidden`} aria-labelledby={headingId}>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-5 pt-5">
            <h2 id={headingId} className="text-[16px] font-semibold tracking-[-0.01em] text-[var(--ink)]">Results</h2>
            {simulated && (
              <span className="rounded-full bg-[var(--warn-tint)] px-2.5 py-0.5 text-[12px] font-medium text-[var(--warn)]">
                Historical synthetic results
              </span>
            )}
            {!decision && <BasisTag basis={basis} />}
          </div>

        {pdfError && (
          <p className="border-b border-[var(--line)] px-5 py-2 text-[12px] text-[var(--danger)]" role="alert">
            {pdfError}
          </p>
        )}

        {["failed", "stopped", "empty", "unavailable"].includes(resultState) && (
          <p className="px-5 py-4 text-[13px] leading-relaxed text-[var(--ink-2)]" role="status">
            {resultState === "failed" && (runId
              ? "This run failed before usable metrics were produced."
              : "Benchmark setup failed before a run was started. Check the benchmark details or provider configuration, then retry.")}
            {resultState === "stopped" && "This run was stopped before usable metrics were produced."}
            {resultState === "empty" && "The run completed without any result metrics."}
            {resultState === "unavailable" && "Result metrics were returned, but no finite primary score is available."}
          </p>
        )}

        {["loading", "ready"].includes(resultState) && (
          <div className="min-w-0 overflow-x-auto">
            <table className="pb-stack-table w-full min-w-[46rem] text-[13px]">
              <caption className="sr-only">Candidate comparison, ranked by the primary metric</caption>
              <thead>
                <tr>
                  <th scope="col" className="px-3 py-2 text-left text-[12px] font-semibold text-[var(--ink-3)]">
                    Rank
                  </th>
                  <th scope="col" className="px-3 py-2 text-left text-[12px] font-semibold text-[var(--ink-3)]">
                    Candidate
                  </th>
                  {columns.map((c) => (
                    <th
                      key={c.key}
                      scope="col"
                      aria-sort={activeSortKey === c.key ? (sortDirection === "asc" ? "ascending" : "descending") : "none"}
                      className={`p-0 text-right text-[12px] font-semibold ${
                        activeSortKey === c.key ? "text-[var(--accent)]" : "text-[var(--ink-3)]"
                      }`}
                    >
                      <button
                        type="button"
                        onClick={() => changeSort(c)}
                        className="inline-flex min-h-10 w-full items-center justify-end px-3 py-2 text-inherit transition-colors duration-150 hover:text-[var(--ink)]"
                        aria-label={`Sort by ${c.label}${activeSortKey === c.key ? `, currently ${sortDirection === "asc" ? "ascending" : "descending"}` : ""}`}
                      >
                        {c.label}
                        {activeSortKey === c.key && <span className="ml-1" aria-hidden="true">{sortDirection === "asc" ? "↑" : "↓"}</span>}
                      </button>
                    </th>
                  ))}
                </tr>
              </thead>
              {resultState === "loading" ? (
                <SkeletonRows columns={columns} />
              ) : (
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.name} className="text-[var(--ink-2)]">
                      <td data-label="Rank" className="px-3 py-3">
                        {r.isWinner ? (
                          <span className="inline-flex h-6 w-6 items-center justify-center rounded-full bg-[var(--ink)] pb-mono text-[11px] font-medium text-[var(--surface)]">
                            {r.canonicalRank || "1"}
                          </span>
                        ) : (
                          <span className="pb-mono text-[13px] text-[var(--ink-3)]">{r.canonicalRank || "-"}</span>
                        )}
                      </td>
                      <td data-primary className="px-3 py-3 font-medium text-[var(--ink)]">
                        {safeVisibleText(r.name)}
                      </td>
                      {columns.map((c) => (
                        <td data-label={c.label} key={c.key} className="pb-mono px-3 py-3 text-right text-[13px]">
                          {c.bar || c.key === "exact_accuracy" || c.key === "field_f1" ? (
                            <div className="flex flex-col items-end gap-1.5">
                              <span>{fmt(c, r[c.key])}</span>
                              <div className="w-16">
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
        )}

        {running && rows.length > 0 && !safeReport?.markdown && (
          <p className="flex items-center gap-2 px-5 py-3 text-[12px] text-[var(--ink-3)]" aria-live="polite">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-[var(--accent)]" aria-hidden="true" />
            Preparing report
          </p>
        )}

        </section>

        {safeReport?.markdown && (
          <section className={`${PANEL} min-w-0 p-5`} aria-labelledby="report-heading">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 id="report-heading" className="text-[16px] font-semibold tracking-[-0.01em] text-[var(--ink)]">Report</h3>
              <div className="flex items-center gap-2">
                {runId && (
                  <button ref={previewButtonRef} onClick={openPdfPreview} disabled={pdfBusy} className={BTN_SECONDARY}>
                    {pdfBusy ? "Preparing PDF" : "Preview PDF"}
                  </button>
                )}
                <button onClick={downloadMarkdown} className={BTN_SECONDARY}>
                  Markdown
                </button>
              </div>
            </div>
            <div className="md md-report pb-contain mt-3 max-w-[75ch] overflow-x-auto text-[13px] text-[var(--ink-2)]">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{ a: SafeMarkdownLink, ...MARKDOWN_HEADINGS_IN_REPORT }}
              >
                {safeVisibleText(safeReport.markdown)}
              </ReactMarkdown>
            </div>
            {safeReport?.citations && safeReport.citations.length > 0 && (
              <div className="mt-5">
                <div className="pb-eyebrow">Citations</div>
                <ul className="mt-2 flex flex-col gap-1">
                  {safeReport.citations.map((c, i) => {
                    const citation = c && typeof c === "object" ? c : { title: c };
                    const safeHref = safeHttpUrl(citation.url);
                    const label = safeVisibleText(citation.title || citation.url);
                    return (
                      <li key={i} className="pb-contain">
                        {safeHref ? (
                          <a href={safeHref} target="_blank" rel="noreferrer" className="text-[12px] text-[var(--accent)] hover:underline">
                            {label}
                          </a>
                        ) : <span className="text-[12px] text-[var(--ink-2)]">{label}</span>}
                      </li>
                    );
                  })}
                </ul>
              </div>
            )}
          </section>
        )}
      </div>
      {pdfModal}
    </>
  );
}
