import { useState } from "react";
import { safeVisibleText } from "../displaySafety.js";
import { phaseTone } from "../phaseLabel.js";
import StatusIcon from "./StatusIcon.jsx";
import { BTN_PRIMARY, BTN_SECONDARY, FlowHighlight, useFlowHighlight } from "./ui.jsx";

function phaseColor(phase) {
  const p = String(phase || "").toLowerCase();
  if (p === "done") return "var(--ok)";
  if (p === "failed") return "var(--danger)";
  if (p === "validating") return "var(--warn)";
  if (p === "intake" || p === "spec_confirm") return "var(--ink-3)";
  return "var(--accent)";
}

/* Compact relative age for a session row: "2h", "3d". Absolute value stays on
   the title attribute for the full timestamp. */
function relativeAge(value) {
  if (!value) return "";
  const then = new Date(value).getTime();
  if (Number.isNaN(then)) return "";
  const secs = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (secs < 60) return "now";
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h`;
  return `${Math.floor(hrs / 24)}d`;
}

/* Row actions are icons: a session row is narrow, and a word-wide button next
   to a truncating title steals the space the title needs. Each keeps its
   accessible name and a 36px hit area. */
const ICON_BTN =
  "inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-[8px] " +
  "focus-visible:outline-none focus-visible:outline-2 focus-visible:outline-offset-1 " +
  "focus-visible:outline-[var(--accent)] disabled:opacity-40";

function TrashIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M4 7h16" />
      <path d="M10 4h4" />
      <path d="M6.5 7l.7 12a1.5 1.5 0 0 0 1.5 1.4h6.6a1.5 1.5 0 0 0 1.5-1.4L17.5 7" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M5 13l4 4L19 7" />
    </svg>
  );
}

function CloseIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M6 6l12 12M18 6L6 18" />
    </svg>
  );
}

export function SessionList({ sessions, activeId, onSelect, onNew, onDelete, onClose, disabled, compact = false }) {
  const [confirmingSessionId, setConfirmingSessionId] = useState(null);
  /* Re-keyed on the list length as well: rows arriving or being deleted shift
     every row below them, and the pill has to follow. */
  const flow = useFlowHighlight(`${activeId}:${sessions.length}:${compact}`);

  return (
    <ul ref={flow.containerRef} className="relative flex flex-col gap-0.5">
      <FlowHighlight box={flow.box} className="pb-session-highlight" />
      {sessions.map((s) => {
        const active = s.id === activeId;
        const safeTitle = safeVisibleText(s.title || "Untitled");
        const confirming = confirmingSessionId === s.id;
        const age = relativeAge(s.created_at);
        return (
          <li
            key={s.id}
            data-flow-active={active ? "true" : undefined}
            className="pb-flow-row group flex items-center gap-1 rounded-[10px]"
          >
            <button
              type="button"
              onClick={() => { onSelect(s.id); onClose?.(); }}
              disabled={disabled}
              className={`flex ${compact ? "min-h-9" : "min-h-11"} min-w-0 flex-1 items-center gap-2 rounded-[10px] px-2.5 py-2 text-left text-[13px] ${
                active ? "font-medium text-[var(--ink)]" : "text-[var(--ink-2)] hover:text-[var(--ink)]"
              }`}
            >
              <span
                className="shrink-0"
                style={{ color: phaseColor(s.phase) }}
                title={safeVisibleText(s.phase)}
              >
                <StatusIcon tone={phaseTone(s.phase)} size={13} />
              </span>
              <span className="min-w-0 flex-1 truncate">{safeTitle}</span>
              {age && (
                <span
                  className="pb-mono shrink-0 text-[11px] text-[var(--ink-3)]"
                  title={new Date(s.created_at).toLocaleString()}
                >
                  {age}
                </span>
              )}
            </button>
            {confirming ? (
              <span
                className="mr-1 flex shrink-0 items-center gap-0.5"
                role="group"
                aria-label={`Confirm deletion of ${safeTitle}`}
              >
                <button
                  type="button"
                  onClick={() => { setConfirmingSessionId(null); onDelete(s); }}
                  disabled={disabled}
                  title="Delete permanently"
                  aria-label={`Confirm deleting ${safeTitle}`}
                  className={`${ICON_BTN} bg-[var(--danger-tint)] text-[var(--danger)] hover:bg-[color-mix(in_oklab,var(--danger)_16%,var(--danger-tint))]`}
                >
                  <CheckIcon />
                </button>
                <button
                  type="button"
                  onClick={() => setConfirmingSessionId(null)}
                  disabled={disabled}
                  title="Keep this session"
                  aria-label={`Cancel deleting ${safeTitle}`}
                  className={`${ICON_BTN} text-[var(--ink-2)] hover:bg-[var(--surface-2)] hover:text-[var(--ink)]`}
                >
                  <CloseIcon />
                </button>
              </span>
            ) : (
              <button
                type="button"
                onClick={() => setConfirmingSessionId(s.id)}
                disabled={s.is_running || disabled}
                className={`${ICON_BTN} mr-1 text-[var(--ink-3)] transition-opacity duration-150 hover:bg-[var(--danger-tint)] hover:text-[var(--danger)] disabled:cursor-not-allowed disabled:opacity-30 md:opacity-0 md:group-focus-within:opacity-100 md:group-hover:opacity-100`}
                title={s.is_running ? "Stop this benchmark before deleting it" : "Delete session"}
                aria-label={`Delete ${safeTitle}`}
              >
                <TrashIcon />
              </button>
            )}
          </li>
        );
      })}
    </ul>
  );
}

/**
 * The small-screen sessions drawer, opened from the Benchmark header. On md and
 * up the list lives in the console's left sidebar instead (Shell.jsx), where a
 * conversation history is conventionally found.
 */
export default function Sidebar({ sessions, activeId, onSelect, onNew, onDelete, onClose, disabled = false }) {
  return (
    <section
      aria-label="Benchmark sessions"
      className="pb-glass-float flex h-full w-[min(340px,calc(100vw-2rem))] shrink-0 flex-col shadow-lift"
    >
      <div className="flex items-center gap-2 border-b border-[var(--line)] px-3 py-3">
        <button type="button" onClick={onNew} disabled={disabled} className={`${BTN_PRIMARY} flex-1`}>
          New benchmark
        </button>
        <button type="button" onClick={onClose} className={BTN_SECONDARY}>
          Close
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-3">
        <div className="pb-eyebrow pb-2">Sessions</div>
        {sessions.length === 0 && (
          <p className="py-2 text-[13px] text-[var(--ink-2)]">
            Start a new benchmark to create a session.
          </p>
        )}
        <SessionList
          sessions={sessions}
          activeId={activeId}
          onSelect={onSelect}
          onDelete={onDelete}
          onClose={onClose}
          disabled={disabled}
        />
      </div>
    </section>
  );
}
