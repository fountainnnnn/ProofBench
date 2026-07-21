import { useState } from "react";
import { safeVisibleText } from "../displaySafety.js";
import { BTN_PRIMARY, BTN_SECONDARY } from "./ui.jsx";

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

function SessionList({ sessions, activeId, onSelect, onNew, onDelete, onClose, disabled, compact = false }) {
  const [confirmingSessionId, setConfirmingSessionId] = useState(null);

  return (
    <ul className="flex flex-col gap-0.5">
      {sessions.map((s) => {
        const active = s.id === activeId;
        const safeTitle = safeVisibleText(s.title || "Untitled");
        const confirming = confirmingSessionId === s.id;
        const age = relativeAge(s.created_at);
        return (
          <li
            key={s.id}
            className={`group flex items-center gap-1 rounded-[10px] ${
              active ? "bg-[var(--surface-2)]" : ""
            }`}
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
                className="h-1.5 w-1.5 shrink-0 rounded-full"
                style={{ backgroundColor: phaseColor(s.phase) }}
                title={safeVisibleText(s.phase)}
              />
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
                className="flex shrink-0 items-center gap-1"
                role="group"
                aria-label={`Confirm deletion of ${safeTitle}`}
              >
                <button
                  type="button"
                  onClick={() => { setConfirmingSessionId(null); onDelete(s); }}
                  disabled={disabled}
                  className="min-h-10 rounded-[8px] px-2 text-[12px] font-medium text-[var(--danger)] hover:bg-[var(--danger-tint)] disabled:opacity-40"
                >
                  Confirm
                </button>
                <button
                  type="button"
                  onClick={() => setConfirmingSessionId(null)}
                  disabled={disabled}
                  className="min-h-10 rounded-[8px] px-2 text-[12px] text-[var(--ink-2)] hover:bg-[var(--surface-2)] disabled:opacity-40"
                >
                  Cancel
                </button>
              </span>
            ) : (
              <button
                type="button"
                onClick={() => setConfirmingSessionId(s.id)}
                disabled={s.is_running || disabled}
                className="mr-1 min-h-10 shrink-0 rounded-[8px] px-2 text-[12px] text-[var(--ink-3)] transition-opacity duration-150 hover:bg-[var(--danger-tint)] hover:text-[var(--danger)] disabled:cursor-not-allowed disabled:opacity-30 md:opacity-0 md:group-focus-within:opacity-100 md:group-hover:opacity-100"
                title={s.is_running ? "Stop this benchmark before deleting it" : "Delete session"}
                aria-label={`Delete ${safeTitle}`}
              >
                Delete
              </button>
            )}
          </li>
        );
      })}
    </ul>
  );
}

/**
 * Session list in two shapes.
 * `variant="rail"`: the persistent right rail on wide screens, resting on the
 * canvas like any other panel. `variant="drawer"` (default): the small-screen
 * dialog, opened from the header.
 */
export default function Sidebar({ sessions, activeId, onSelect, onNew, onDelete, onClose, disabled = false, variant = "drawer" }) {
  if (variant === "rail") {
    return (
      <section
        aria-label="Sessions"
        className="flex h-full w-full flex-col"
      >
        <div className="flex max-h-full min-h-0 flex-col">
          <div className="flex shrink-0 items-baseline justify-between gap-2 px-2 pb-2 pt-1">
            <h2 className="pb-eyebrow">Sessions</h2>
            <span className="pb-mono text-[12px] text-[var(--ink-3)]">{sessions.length}</span>
          </div>
          {sessions.length === 0 ? (
            <p className="px-2 pb-2 pt-1 text-[13px] text-[var(--ink-2)]">
              Start a new benchmark to create a session.
            </p>
          ) : (
            <div className="min-h-0 overflow-y-auto pb-1">
              <SessionList
                sessions={sessions}
                activeId={activeId}
                onSelect={onSelect}
                onDelete={onDelete}
                disabled={disabled}
                compact
              />
            </div>
          )}
        </div>
      </section>
    );
  }

  return (
    <section
      aria-label="Benchmark sessions"
      className="flex h-full w-[min(340px,calc(100vw-2rem))] shrink-0 flex-col border-r border-[var(--line)] bg-[var(--surface)] shadow-lift"
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
