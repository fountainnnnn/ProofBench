import { useMemo, useState } from "react";
import { PANEL } from "./ui.jsx";
import { safeVisibleText, sanitizeForDisplay } from "../displaySafety.js";
import { safeHttpUrl } from "../linkSafety.js";
import { phaseLabel, phaseTone as phaseToneKey } from "../phaseLabel.js";
import StatusIcon from "./StatusIcon.jsx";
import {
  DETAIL_EXPANDED_CHARS,
  DETAIL_PREVIEW_CHARS,
  condenseDetail,
  summarizeTrace,
  traceSummaryText,
} from "../traceSummary.js";

const DISPLAY_LOG_LINES = 250;

/* Phase -> accessible text/tint pair: building and running are informational,
   validating is a warning, done is ok, failed is danger. */
const PHASE_TONE = {
  done: "bg-[var(--ok-tint)] text-[var(--ok)]",
  failed: "bg-[var(--danger-tint)] text-[var(--danger)]",
  validating: "bg-[var(--warn-tint)] text-[var(--warn)]",
  building: "bg-[var(--accent-tint)] text-[var(--accent)]",
  running: "bg-[var(--accent-tint)] text-[var(--accent)]",
};

function phaseTone(phase) {
  return PHASE_TONE[String(phase || "").toLowerCase()] ||
    "bg-[var(--surface-2)] text-[var(--ink-2)]";
}

function phaseDot(phase) {
  const p = String(phase || "").toLowerCase();
  if (p === "done") return "var(--ok)";
  if (p === "failed") return "var(--danger)";
  if (p === "validating") return "var(--warn)";
  if (p === "building" || p === "running") return "var(--accent)";
  return "var(--ink-3)";
}

const TRACE_STATUS = {
  start: { color: "var(--accent)", tone: "running", pulse: true },
  ok: { color: "var(--ok)", tone: "ok", pulse: false },
  error: { color: "var(--danger)", tone: "danger", pulse: false },
};

const disclosureButton = "flex w-full items-center gap-2 text-left";

function Caret({ open }) {
  return (
    <span className="select-none text-[var(--ink-3)]" aria-hidden="true">
      {open ? "▾" : "▸"}
    </span>
  );
}

function PhaseBadge({ label }) {
  const safeLabel = safeVisibleText(label);
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[12px] font-medium ${phaseTone(safeLabel)}`}
    >
      <StatusIcon tone={phaseToneKey(safeLabel)} size={12} />
      {phaseLabel(safeLabel)}
    </span>
  );
}

function SandboxPanel({ name, lines, defaultOpen }) {
  const [open, setOpen] = useState(defaultOpen);
  const safeLines = Array.isArray(lines) ? lines : [];
  const phase = safeLines.length ? safeLines[safeLines.length - 1].phase : "building";
  const visibleLines = safeLines.slice(-DISPLAY_LOG_LINES);
  const hiddenCount = safeLines.length - visibleLines.length;
  return (
    <div className="rounded-[12px] bg-[var(--surface-2)]">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className={`${disclosureButton} min-h-10 justify-between rounded-[12px] px-3 py-2`}
      >
        <span className="pb-mono flex min-w-0 items-center gap-2 text-[13px] text-[var(--ink)]">
          <Caret open={open} />
          <span className="truncate">{safeVisibleText(name)}</span>
          <span className="shrink-0 text-[12px] text-[var(--ink-3)]">
            {safeLines.length} {safeLines.length === 1 ? "line" : "lines"}
          </span>
        </span>
        <PhaseBadge label={phase} />
      </button>
      {open && (
        <div className="pb-mono mx-2 mb-2 max-h-56 overflow-y-auto rounded-[12px] bg-[var(--code-bg)] px-3 py-2 text-[12px] leading-relaxed text-[var(--code-text)]">
          {safeLines.length === 0 && (
            <div className="h-3 w-32 animate-pulse bg-[color-mix(in_oklab,var(--code-text)_15%,transparent)]" />
          )}
          {hiddenCount > 0 && (
            <div className="mb-1 text-[color-mix(in_oklab,var(--code-text)_55%,transparent)]">
              {hiddenCount} older lines omitted
            </div>
          )}
          {visibleLines.map((l, i) => {
            const failed = l.phase === "failed" || /error|fail|traceback/i.test(l.line);
            const repair = !failed && /repair|retry|attempt/i.test(l.line);
            return (
              <div
                key={i}
                className={`pb-long-list-item pb-contain ${
                  failed
                    ? "text-[color-mix(in_oklab,var(--danger)_55%,var(--code-text))]"
                    : repair
                    ? "text-[color-mix(in_oklab,var(--warn)_55%,var(--code-text))]"
                    : ""
                }`}
              >
                <span className="mr-2 select-none text-[color-mix(in_oklab,var(--code-text)_35%,transparent)]">
                  $
                </span>
                {safeVisibleText(l.line)}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function linkify(text) {
  return text.split(/(https?:\/\/[^\s]+)/g).map((part, index) => {
    const href = safeHttpUrl(part);
    return href ? (
      <a
        key={index}
        href={href}
        target="_blank"
        rel="noreferrer"
        className="break-all text-[var(--accent)] hover:underline"
      >
        {part}
      </a>
    ) : (
      part
    );
  });
}

/* Scraped documentation arrives here as raw markup. It is reduced to bounded
   plain text, and more of it is only ever shown on request. */
function TraceDetail({ children }) {
  const [expanded, setExpanded] = useState(false);
  const preview = useMemo(() => condenseDetail(children, DETAIL_PREVIEW_CHARS), [children]);
  const full = useMemo(
    () => (expanded ? condenseDetail(children, DETAIL_EXPANDED_CHARS) : null),
    [children, expanded]
  );
  if (!preview.text) return null;
  const shown = expanded && full ? full : preview;
  return (
    <div className="mt-0.5 text-[12px] leading-relaxed text-[var(--ink-2)]">
      <span className="pb-contain">{linkify(safeVisibleText(shown.text))}</span>
      {preview.truncated && (
        <>
          {!expanded && <span aria-hidden="true">&hellip;</span>}{" "}
          <button
            type="button"
            onClick={() => setExpanded((value) => !value)}
            aria-expanded={expanded}
            className="rounded-[12px] text-[12px] font-medium text-[var(--accent)] hover:underline"
          >
            {expanded ? "Show less" : `Show more (${preview.length} characters)`}
          </button>
          {expanded && shown.truncated && (
            <span className="ml-1 text-[11px] text-[var(--ink-3)]">
              {shown.hidden} characters not shown
            </span>
          )}
        </>
      )}
    </div>
  );
}

function TraceGroup({ group, defaultOpen }) {
  const [open, setOpen] = useState(defaultOpen);
  const tone = group.errors > 0 ? "error" : group.pending > 0 ? "start" : "ok";
  const st = TRACE_STATUS[tone];
  const counts = [
    `${group.calls} ${group.calls === 1 ? "call" : "calls"}`,
    group.ok > 0 ? `${group.ok} ok` : null,
    group.errors > 0 ? `${group.errors} failed` : null,
    group.pending > 0 ? `${group.pending} in progress` : null,
  ].filter(Boolean).join(", ");

  return (
    <div className="rounded-[12px] bg-[var(--surface-2)]">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className={`${disclosureButton} min-h-10 justify-between rounded-[12px] px-3 py-2`}
      >
        <span className="flex min-w-0 items-center gap-2">
          <Caret open={open} />
          <span className="shrink-0" style={{ color: st.color }}>
            <StatusIcon tone={st.tone} size={13} pulse={st.pulse} />
          </span>
          <span className="pb-mono truncate text-[13px] text-[var(--ink)]">
            {safeVisibleText(group.tool)}
          </span>
        </span>
        <span className="shrink-0 text-[12px] text-[var(--ink-2)]">{counts}</span>
      </button>
      {open && (
        <div className="flex flex-col gap-2 px-3 pb-3 pt-1">
          {group.hidden > 0 && (
            <p className="text-[11px] text-[var(--ink-3)]">
              {group.hidden} earlier {group.hidden === 1 ? "call" : "calls"} not shown.
            </p>
          )}
          {group.items.map((item) => {
            const itemStatus = TRACE_STATUS[item.status] || TRACE_STATUS.start;
            return (
              <div key={item.index} className="pb-long-list-item flex items-start gap-2">
                <span className="mt-0.5 shrink-0" style={{ color: itemStatus.color }}>
                  <StatusIcon tone={itemStatus.tone} size={12} pulse={itemStatus.pulse} />
                </span>
                <div className="min-w-0 flex-1">
                  {item.args_summary && (
                    <div className="pb-contain text-[12px] text-[var(--ink)]">
                      {safeVisibleText(item.args_summary)}
                    </div>
                  )}
                  {item.detail && <TraceDetail>{item.detail}</TraceDetail>}
                  {!item.args_summary && !item.detail && (
                    <div className="text-[12px] text-[var(--ink-3)]">No detail reported.</div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

export default function AgentTraceCard({
  trace,
  sandboxLogs,
  phaseState,
  simulated,
  defaultOpen = true,
  headingId,
}) {
  const safeTrace = useMemo(() => sanitizeForDisplay(trace || []), [trace]);
  const safeLogs = useMemo(() => sanitizeForDisplay(sandboxLogs || {}), [sandboxLogs]);
  const safePhase = useMemo(() => sanitizeForDisplay(phaseState || null), [phaseState]);
  const groups = useMemo(() => summarizeTrace(safeTrace), [safeTrace]);
  const sandboxes = Object.keys(safeLogs || {});
  const [open, setOpen] = useState(defaultOpen);
  const summary = traceSummaryText(safeTrace, sandboxes.length);

  return (
    <section className={`${PANEL} min-w-0 p-5`} aria-labelledby={headingId}>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className={`${disclosureButton} flex-wrap justify-between rounded-[12px]`}
      >
        <span className="flex min-w-0 flex-wrap items-center gap-2">
          <Caret open={open} />
          <span id={headingId} className="text-[16px] font-semibold tracking-[-0.01em] text-[var(--ink)]">
            Execution trace
          </span>
          {safePhase?.phase && <PhaseBadge label={safePhase.phase} />}
          {simulated && (
            <span className="rounded-full bg-[var(--warn-tint)] px-2.5 py-0.5 text-[12px] font-medium text-[var(--warn)]">
              Historical synthetic trace
            </span>
          )}
        </span>
        {summary && <span className="text-[12px] text-[var(--ink-2)]">{summary}</span>}
      </button>

      {open && (
        <div className="mt-4 flex flex-col gap-3">
          {safePhase?.candidates && (
            <ul className="flex flex-wrap gap-x-4 gap-y-1">
              {Object.entries(safePhase.candidates).map(([name, st]) => (
                <li key={name} className="inline-flex items-center gap-1.5 text-[12px] text-[var(--ink-2)]">
                  <span className="shrink-0" style={{ color: phaseDot(st) }}>
                    <StatusIcon tone={phaseToneKey(st)} size={12} />
                  </span>
                  <span className="pb-contain">
                    {safeVisibleText(name)}: {safeVisibleText(st)}
                  </span>
                </li>
              ))}
            </ul>
          )}

          {groups.length > 0 && (
            <div className="flex flex-col gap-2">
              {groups.map((group) => (
                <TraceGroup key={group.tool} group={group} defaultOpen={defaultOpen} />
              ))}
            </div>
          )}

          {sandboxes.length > 0 && (
            <div className="flex flex-col gap-2">
              {sandboxes.map((name) => (
                <SandboxPanel key={name} name={name} lines={safeLogs[name]} defaultOpen={defaultOpen} />
              ))}
            </div>
          )}

          {groups.length === 0 && sandboxes.length === 0 && (
            <p className="text-[12px] text-[var(--ink-3)]">
              No tool calls or sandbox output were recorded for this run.
            </p>
          )}
        </div>
      )}
    </section>
  );
}
