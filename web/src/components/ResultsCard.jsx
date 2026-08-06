import { Children, cloneElement, isValidElement, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { prepareReportPdf } from "../api.js";
import { BasisTag, BTN_PRIMARY, BTN_SECONDARY, MARKDOWN_HEADINGS_IN_REPORT, PANEL } from "./ui.jsx";
import { safeVisibleText, sanitizeForDisplay } from "../displaySafety.js";
import StatusIcon from "./StatusIcon.jsx";
import { safeHttpUrl } from "../linkSafety.js";
import { repairLegacyReportTables, splitReportFindings } from "../reportMarkdown.js";
import {
  buildCanonicalRows,
  buildDecision,
  candidateLabel,
  componentRows,
  classifyResultsState,
  evidenceTier,
  executionBasisLabel,
  sortResultRows,
} from "../resultsModel.js";

/* Sandbox measurements. Only a candidate that actually ran has any of these, so
   in a run where most candidates could not be reached they are seven columns of
   "Unavailable" — the table stops being a comparison and becomes a list of
   blanks. They collapse behind one control instead. */
const OCR_MEASURED_COLUMNS = [
  { key: "exact_accuracy", label: "Exact acc.", pct: true, higher: true, bar: true },
  { key: "field_f1", label: "F1", pct: true, higher: true, bar: true },
  { key: "cer", label: "CER", pct: true, higher: false },
  { key: "mean_latency_s", label: "Latency (s)", higher: false },
  { key: "failure_rate", label: "Failure", pct: true, higher: false },
  { key: "cost_per_1k_docs", label: "Cost/1k", higher: false },
];

/* Documentation evidence, which every candidate has whether or not it ran. This
   is what makes the table comparable at all when execution was blocked. */
const OCR_RESEARCH_COLUMNS = [
  { key: "research_score", label: "Docs score", higher: true, score: true, bar: true },
  { key: "documentation_quality", label: "Docs", higher: true, score: true },
  { key: "integration_feasibility", label: "Integration", higher: true, score: true },
  { key: "auth_clarity", label: "Auth", higher: true, score: true },
  { key: "setup_complexity", label: "Setup", higher: false },
];

const OCR_COLUMNS = [...OCR_MEASURED_COLUMNS, ...OCR_RESEARCH_COLUMNS];

const EVIDENCE_LABELS = { measured: "Measured", research: "Documentation" };

/* Rank is the default order because it is the one the verdict above is stated
   in: measured candidates first, then those scored from their documentation. */
const RANK_COLUMN = { key: "canonicalRank", label: "Rank", higher: false };

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

/* Measured or documented, said once per row. */
function EvidenceTag({ tier }) {
  const label = EVIDENCE_LABELS[tier];
  if (!label) return null;
  const measured = tier === "measured";
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ${
        measured
          ? "bg-[var(--accent-tint)] text-[var(--accent)]"
          : "bg-[var(--surface-2)] text-[var(--ink-3)]"
      }`}
    >
      {label}
    </span>
  );
}

/* One control for the whole sandbox column group, stating the ratio it hides.
   "2 of 6 ran" is the fact a reader needs before they read any of it. */
function MeasurementToggle({ open, measured, total, onToggle }) {
  if (total === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-5 pb-1 pt-3">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="inline-flex min-h-8 items-center gap-1.5 rounded-full border border-[var(--line)] px-3 text-[12px] font-medium text-[var(--ink-2)] transition-colors duration-150 hover:text-[var(--ink)]"
      >
        <span aria-hidden="true">{open ? "−" : "+"}</span>
        {open ? "Hide sandbox measurements" : "Show sandbox measurements"}
      </button>
      <span className="text-[12px] text-[var(--ink-3)]">
        {measured} of {total} ran
        {measured < total && " · the rest are scored from their documentation"}
      </span>
    </div>
  );
}

/* Candidates that produced no measurement are not poor performers, and a row of
   seven "Unavailable" cells is not a comparison — it is most of the table's
   height spent saying nothing, with a stack-trace in the name column pushing
   the candidates that did run off the top. They are named here instead, with
   their reason, so the table above stays the comparison it claims to be. */
function NotCompared({ rows }) {
  if (rows.length === 0) return null;
  return (
    <div className="border-t border-[var(--line)] px-5 py-4">
      <div className="pb-eyebrow">Not compared · {rows.length}</div>
      <ul className="mt-2 flex flex-col gap-1.5">
        {rows.map((r) => {
          const reason = safeVisibleText(r.error_summary || "");
          return (
            <li key={r.name} className="flex flex-wrap items-baseline gap-x-2 text-[12px] leading-relaxed">
              <span className="font-medium text-[var(--ink-2)]">{safeVisibleText(candidateLabel(r))}</span>
              <span
                className="pb-contain line-clamp-1 min-w-0 flex-1 text-[var(--ink-3)]"
                title={reason || undefined}
              >
                {reason ? `Did not run: ${reason}` : "Did not run"}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function SkeletonRows({ columns, evidence = false }) {
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
          {evidence && (
            <td className="px-3 py-3">
              <div className="pb-skeleton h-3 w-16" />
            </td>
          )}
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

const REPORT_STATUS = {
  passed: { key: "passed", label: "Verified", tone: "ok", icon: "check" },
  failed: { key: "failed", label: "Verification failed", tone: "danger", icon: "x" },
  not_implementable: {
    key: "not_implementable",
    label: "Not implementable",
    tone: "warn",
    icon: "slash",
  },
  not_applicable: {
    key: "not_applicable",
    label: "Not applicable",
    tone: "muted",
    icon: "minus",
  },
};

const REPORT_STATUS_ORDER = ["passed", "failed", "not_implementable", "not_applicable"];

function reportNodeText(node) {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(reportNodeText).join("");
  if (isValidElement(node)) return reportNodeText(node.props.children);
  return "";
}

function reportCellStatus(header, value) {
  const column = String(header || "").trim().toLowerCase();
  const normalized = String(value || "").trim().toLowerCase().replace(/\s+/g, "_");
  if (column === "verification") {
    return REPORT_STATUS[normalized] || null;
  }
  if (column === "pricing" && ["n/a", "not_applicable", "not_applicable."].includes(normalized)) {
    return REPORT_STATUS.not_applicable;
  }
  return null;
}

function ReportStatusIcon({ status, compact = false }) {
  if (status.icon === "slash") {
    return (
      <span
        role="img"
        aria-label={status.label}
        title={status.label}
        className={`inline-flex shrink-0 align-middle items-center justify-center ${
          compact ? "h-4 w-4" : "h-5 w-5"
        }`}
      >
        <img
          src="/status/not-implementable-v2.png"
          alt=""
          width={compact ? 16 : 20}
          height={compact ? 16 : 20}
          className="block h-full w-full"
        />
      </span>
    );
  }

  const tone = {
    ok: "bg-[var(--ok-tint)] text-[var(--ok)]",
    danger: "bg-[var(--danger-tint)] text-[var(--danger)]",
    warn: "bg-[var(--warn-tint)] text-[var(--warn)]",
    muted: "bg-[var(--surface-2)] text-[var(--ink-3)]",
  }[status.tone];
  return (
    <span
      role="img"
      aria-label={status.label}
      title={status.label}
      className={`inline-flex shrink-0 align-middle items-center justify-center rounded-full ${tone} ${
        compact ? "h-4 w-4" : "h-5 w-5"
      }`}
    >
      <svg
        aria-hidden="true"
        width={compact ? 10 : 12}
        height={compact ? 10 : 12}
        viewBox="0 0 16 16"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
        className="block"
      >
        {status.icon === "check" && <path d="m3.5 8.25 2.75 2.75 6.25-6.25" />}
        {status.icon === "x" && <path d="m4.5 4.5 7 7m0-7-7 7" />}
        {status.icon === "minus" && <path d="M4 8h8" />}
      </svg>
    </span>
  );
}

/* The named scroll region follows the WAI keyboard-scroll technique. The lint
   rule does not model that exception. */
/* eslint-disable jsx-a11y/no-noninteractive-tabindex */
function ReportTable({ node: _node, ...props }) {
  const tableChildren = Children.toArray(props.children);
  const head = tableChildren.find((child) => isValidElement(child) && child.type === "thead");
  const headRow = head
    ? Children.toArray(head.props.children).find((child) => isValidElement(child) && child.type === "tr")
    : null;
  const headers = headRow
    ? Children.toArray(headRow.props.children).map((cell) => reportNodeText(cell).trim())
    : [];
  const usedStatuses = new Set();
  const enhancedChildren = tableChildren.map((group) => {
    if (!isValidElement(group) || group.type !== "tbody") return group;
    const rows = Children.toArray(group.props.children).map((row) => {
      if (!isValidElement(row) || row.type !== "tr") return row;
      const cells = Children.toArray(row.props.children).map((cell, index) => {
        if (!isValidElement(cell) || cell.type !== "td") return cell;
        const status = reportCellStatus(headers[index], reportNodeText(cell));
        if (!status) return cell;
        usedStatuses.add(status.key);
        return cloneElement(
          cell,
          { ...cell.props, "data-report-status": status.key },
          <ReportStatusIcon status={status} />
        );
      });
      return cloneElement(row, row.props, cells);
    });
    return cloneElement(group, group.props, rows);
  });
  const legend = REPORT_STATUS_ORDER
    .filter((key) => usedStatuses.has(key))
    .map((key) => REPORT_STATUS[key]);

  return (
    <>
      {/* A report can compare enough dimensions to exceed the reading column.
          The table owns that overflow instead of narrowing every column or
          making the report's headings and paragraphs scroll with it. */}
      <div
        role="region"
        aria-label="Report table"
        tabIndex={0}
        className="max-w-full overflow-x-auto overscroll-x-contain focus-visible:outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)]"
      >
        <table {...props}>{enhancedChildren}</table>
      </div>
      {legend.length > 0 && (
        <div
          aria-label="Table status legend"
          className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[11px] text-[var(--ink-3)]"
        >
          {legend.map((status) => (
            <span key={status.key} className="inline-flex items-center gap-1.5">
              <ReportStatusIcon status={status} compact />
              {status.label}
            </span>
          ))}
        </div>
      )}
    </>
  );
}
/* eslint-enable jsx-a11y/no-noninteractive-tabindex */

const REPORT_MARKDOWN_COMPONENTS = {
  a: SafeMarkdownLink,
  table: ReportTable,
  ...MARKDOWN_HEADINGS_IN_REPORT,
};

function ReportMarkdown({ markdown }) {
  const repaired = repairLegacyReportTables(markdown);
  const grouped = splitReportFindings(repaired);
  if (!grouped) {
    return (
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={REPORT_MARKDOWN_COMPONENTS}>
        {repaired}
      </ReactMarkdown>
    );
  }

  return (
    <>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={REPORT_MARKDOWN_COMPONENTS}>
        {grouped.before}
      </ReactMarkdown>
      {grouped.intro && (
        <ReactMarkdown remarkPlugins={[remarkGfm]} components={REPORT_MARKDOWN_COMPONENTS}>
          {grouped.intro}
        </ReactMarkdown>
      )}
      <div className="mt-3" data-report-findings>
        {grouped.findings.map((finding, index) => (
          <section
            key={`${finding.match(/^###\s+(.+)/)?.[1] || "finding"}-${index}`}
            className="pb-finding-section"
            data-report-finding
          >
            <span className="pb-finding-number" aria-hidden="true">
              {index + 1}.
            </span>
            <div className="pb-finding-content">
              <ReactMarkdown remarkPlugins={[remarkGfm]} components={REPORT_MARKDOWN_COMPONENTS}>
                {finding}
              </ReactMarkdown>
            </div>
          </section>
        ))}
      </div>
      {grouped.after && (
        <div className="mt-5">
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={REPORT_MARKDOWN_COMPONENTS}>
            {grouped.after}
          </ReactMarkdown>
        </div>
      )}
    </>
  );
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
      ? `Level with ${safeVisibleText(candidateLabel(decision.runnerUp))} on this metric`
      : column.pct
        ? `${roundedMargin.toFixed(1)} points ahead of ${safeVisibleText(candidateLabel(decision.runnerUp))}`
        : `${roundedMargin} ahead of ${safeVisibleText(candidateLabel(decision.runnerUp))}`;

  /* Nothing cleared the bar. The eyebrow stops saying "Recommendation", the
     headline states the outcome instead of naming a winner, and the reason the
     assessment itself gave is quoted — it is the specific requirement that went
     unmet, which is the only actionable thing on the card. */
  const failedReason = safeVisibleText(decision.winner?.reason || "");
  const buildNames = (decision.buildPath || [])
    .map((row) => safeVisibleText(candidateLabel(row)))
    .filter(Boolean);

  return (
    <div className="pb-verdict-enter rounded-[20px] bg-[var(--hero-ink)] p-6 shadow-[var(--shadow-card)]">
      <p className="text-[11px] font-semibold uppercase tracking-[0.06em] text-[var(--hero-ink-2)]">
        {decision.unmet ? "No recommendation" : "Recommendation"}
      </p>
      {decision.unmet ? (
        <>
          <div className="mt-2">
            <span className="pb-display pb-contain text-[28px] leading-tight text-[var(--hero-text)] sm:text-[32px]">
              {buildNames.length > 0
                ? "No marketed product met the requirements"
                : "No candidate met the requirements"}
            </span>
          </div>
          <p className="mt-2 max-w-[70ch] text-[14px] leading-relaxed text-[var(--hero-ink-2)]">
            {`${decision.failedCount} of ${decision.rankedCount} candidates were rated not implementable against the stated objective. `}
            {buildNames.length > 0
              ? `${buildNames.join(", ")} ${buildNames.length === 1 ? "is" : "are"} documented well enough to build against instead.`
              : `${safeVisibleText(candidateLabel(decision.winner))} scored highest at ${winnerScore} on documentation alone; the ranking below is relative, not an endorsement.`}
          </p>
          {failedReason && (
            <p className="mt-2 max-w-[70ch] text-[13px] leading-relaxed text-[var(--hero-ink-2)]">
              {failedReason}
            </p>
          )}
        </>
      ) : (
        <>
          <div className="mt-2 flex flex-wrap items-baseline gap-x-4 gap-y-1">
            <span className="pb-display pb-contain text-[34px] leading-tight text-[var(--hero-text)] sm:text-[38px]">
              {safeVisibleText(candidateLabel(decision.winner))}
            </span>
            <span className="pb-mono text-[16px] font-medium text-[var(--hero-text)]">
              {column.label ? `${column.label} ${winnerScore}` : winnerScore}
            </span>
          </div>
          <p className="mt-2 max-w-[70ch] text-[14px] leading-relaxed text-[var(--hero-ink-2)]">
            {marginText
              ? `${marginText}. Ranked first of ${decision.rankedCount}.`
              : `Ranked first of ${decision.rankedCount}. No comparable runner up.`}
            {/* How many of those the run could actually execute. Without it,
                "first of 6" reads as six benchmarked products when two ran. */}
            {decision.researchCount > 0 && (
              ` ${decision.measuredCount} measured, ${decision.researchCount} scored from documentation.`
            )}
          </p>
        </>
      )}
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
  const [sortKey, setSortKey] = useState("canonicalRank");
  const [sortDirection, setSortDirection] = useState("asc");
  const [showMeasured, setShowMeasured] = useState(null);
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

  const canonicalRows = useMemo(
    () => buildCanonicalRows(safeMetrics, isAssessment),
    [safeMetrics, isAssessment]
  );
  /* Only a candidate with evidence behind it is a table row. An unranked row has
     neither a measurement nor a readable documentation set: every cell in it
     reads "Unavailable" and its bar reads as a zero it never scored. */
  const ranked = useMemo(() => canonicalRows.filter((row) => row.canonicalRank), [canonicalRows]);
  const unrankedRows = useMemo(
    () => canonicalRows.filter((row) => !row.canonicalRank),
    [canonicalRows]
  );
  const measuredCount = useMemo(
    () => ranked.filter((row) => evidenceTier(row, isAssessment) === "measured").length,
    [ranked, isAssessment]
  );
  /* Expanded when every ranked candidate ran, because then these columns are the
     comparison. Collapsed the moment any candidate could not be reached, because
     then they are mostly blanks and the documentation scores carry the table.
     An explicit click always wins over the default. */
  const measuredOpen = showMeasured ?? (measuredCount > 0 && measuredCount === ranked.length);
  const columns = useMemo(() => (
    isAssessment
      ? ASSESSMENT_COLUMNS
      : [...(measuredOpen ? OCR_MEASURED_COLUMNS : []), ...OCR_RESEARCH_COLUMNS]
  ), [isAssessment, measuredOpen]);
  const sortable = useMemo(() => [RANK_COLUMN, ...columns], [columns]);
  const activeSortKey = sortable.some((column) => column.key === sortKey)
    ? sortKey
    : RANK_COLUMN.key;
  const rankedRows = useMemo(
    () => sortResultRows(ranked, activeSortKey, sortDirection),
    [ranked, activeSortKey, sortDirection]
  );
  /* Libraries are not table rows — they are parts of one self-built design, and
     the report's plan is where that design lives. The decision still needs them
     to say building is the answer, so they are read straight from the metrics. */
  const decision = useMemo(
    () => (resultState === "ready"
      ? buildDecision(canonicalRows, isAssessment, componentRows(safeMetrics, isAssessment))
      : null),
    [canonicalRows, safeMetrics, isAssessment, resultState]
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
            className="pb-glass-float flex h-[min(48rem,calc(100dvh-1rem))] w-full max-w-canvas flex-col overflow-hidden rounded-[24px] shadow-[var(--shadow-lift)]"
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
            /* What the verdict itself rests on. A winner nothing could execute
               is presented as documentation evidence, never as a measurement. */
            evidence={simulated
              ? "Historical synthetic"
              : decision.evidence === "research" ? "Documentation" : "Measured"}
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

        {resultState === "ready" && !isAssessment && (
          <MeasurementToggle
            open={measuredOpen}
            measured={measuredCount}
            total={ranked.length}
            onToggle={() => setShowMeasured(!measuredOpen)}
          />
        )}

        {(resultState === "loading" || (resultState === "ready" && rankedRows.length > 0)) && (
          <div className="min-w-0 overflow-x-auto">
            <table className={`pb-stack-table w-full min-w-[46rem] text-[13px] ${isAssessment ? "table-fixed" : ""}`}>
              <caption className="sr-only">
                Candidate comparison, ranked by evidence then score
              </caption>
              {isAssessment && (
                <colgroup>
                  <col className="w-[6%]" />
                  <col className="w-[26%]" />
                  <col className="w-[18%]" />
                  <col className="w-[11%]" />
                  <col className="w-[8%]" />
                  <col className="w-[9%]" />
                  <col className="w-[8%]" />
                  <col className="w-[5%]" />
                  <col className="w-[9%]" />
                </colgroup>
              )}
              <thead>
                <tr>
                  <th
                    scope="col"
                    aria-sort={activeSortKey === RANK_COLUMN.key ? (sortDirection === "asc" ? "ascending" : "descending") : "none"}
                    className={`p-0 text-left text-[12px] font-semibold ${
                      activeSortKey === RANK_COLUMN.key ? "text-[var(--accent)]" : "text-[var(--ink-3)]"
                    }`}
                  >
                    <button
                      type="button"
                      onClick={() => changeSort(RANK_COLUMN)}
                      className="inline-flex min-h-10 items-center px-3 py-2 text-inherit transition-colors duration-150 hover:text-[var(--ink)]"
                      aria-label={`Sort by rank${activeSortKey === RANK_COLUMN.key ? `, currently ${sortDirection === "asc" ? "ascending" : "descending"}` : ""}`}
                    >
                      Rank
                      {activeSortKey === RANK_COLUMN.key && <span className="ml-1" aria-hidden="true">{sortDirection === "asc" ? "↑" : "↓"}</span>}
                    </button>
                  </th>
                  <th scope="col" className="px-3 py-2 text-left text-[12px] font-semibold text-[var(--ink-3)]">
                    Candidate
                  </th>
                  {!isAssessment && (
                    <th scope="col" className="px-3 py-2 text-left text-[12px] font-semibold text-[var(--ink-3)]">
                      Evidence
                    </th>
                  )}
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
                <SkeletonRows columns={columns} evidence={!isAssessment} />
              ) : (
                <tbody>
                  {rankedRows.map((r) => (
                    <tr key={r.name} className="text-[var(--ink-2)]">
                      <td data-label="Rank" className="px-3 py-3">
                        {r.isWinner ? (
                          <span className="inline-flex h-6 w-6 items-center justify-center rounded-full bg-[var(--ink)] pb-mono text-[11px] font-medium text-[var(--surface)]">
                            {r.canonicalRank || "1"}
                          </span>
                        ) : (
                          <span className="pb-mono text-[13px] text-[var(--ink-3)]">{r.canonicalRank}</span>
                        )}
                      </td>
                       <td data-primary className="px-3 py-3 font-medium text-[var(--ink)]">
                         {safeVisibleText(candidateLabel(r))}
                       </td>
                      {/* Which kind of evidence this row's numbers came from.
                          Without it a documentation score and a measured
                          accuracy sit in one table looking equally proven. */}
                      {!isAssessment && (
                        <td data-label="Evidence" className="px-3 py-3">
                          <EvidenceTag tier={evidenceTier(r, isAssessment)} />
                        </td>
                      )}
                      {columns.map((c) => {
                        const value = r[c.key];
                        const numeric = typeof value === "number" && Number.isFinite(value);
                        const missing = value === null || value === undefined || (typeof value === "number" && !numeric);
                        const barred = c.bar || c.key === "exact_accuracy" || c.key === "field_f1";
                        return (
                          <td data-label={c.label} data-column={c.key} key={c.key} className="pb-mono px-3 py-3 text-right text-[13px]">
                            {/* No bar without a number behind it: an empty track
                                beside "Unavailable" draws a zero never scored. */}
                            {barred && numeric ? (
                              <div className="flex flex-col items-end gap-1.5">
                                <span>{fmt(c, value)}</span>
                                <div className={isAssessment && c.key === "rating" ? "w-16 min-[721px]:w-full" : "w-16"}>
                                  <Bar value={value} max={c.score ? 100 : 1} />
                                </div>
                              </div>
                            ) : missing ? (
                              <span className="text-[var(--ink-3)]">{fmt(c, value)}</span>
                            ) : (
                              fmt(c, value)
                            )}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              )}
            </table>
          </div>
        )}

        {resultState === "ready" && <NotCompared rows={unrankedRows} />}

        {running && rankedRows.length > 0 && !safeReport?.markdown && (
          <p className="flex items-center gap-2 px-5 py-3 text-[12px] text-[var(--ink-3)]" aria-live="polite">
            <StatusIcon tone="running" size={13} pulse className="text-[var(--accent)]" />
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
            <div className="md md-report pb-contain mt-3 min-w-0 text-[13px] text-[var(--ink-2)]">
              <ReportMarkdown markdown={safeVisibleText(safeReport.markdown)} />
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
