/* The full execution log, in a side panel.
 *
 * The collapsed activity line in the thread is the summary; this is where the
 * evidence behind it lives. It opens beside the conversation rather than
 * expanding inside it, so reading the log never pushes the answer off screen
 * and closing it returns you exactly where you were.
 */

import { useEffect, useMemo, useRef } from "react";
import { sanitizeForDisplay, safeVisibleText } from "../displaySafety.js";
import { safeHttpUrl } from "../linkSafety.js";
import { traceSources } from "../traceSummary.js";

export default function TracePanel({ open, onClose, trace, simulated }) {
  const sources = useMemo(() => traceSources(sanitizeForDisplay(trace || [])), [trace]);
  const panelRef = useRef(null);
  const closeRef = useRef(null);
  const restoreRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    restoreRef.current = document.activeElement;
    closeRef.current?.focus();

    const onKeyDown = (event) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onClose();
        return;
      }
      /* Focus stays inside while it is open: it is a dialog over the thread,
         and tabbing out of it would land the reader on controls they cannot
         see. */
      if (event.key !== "Tab") return;
      const focusable = panelRef.current?.querySelectorAll(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
      );
      if (!focusable || focusable.length === 0) return;
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

    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
      const restore = restoreRef.current;
      if (restore && typeof restore.focus === "function") restore.focus();
    };
  }, [open, onClose]);

  if (!open) return null;

  /* Fixed, not absolute: this renders inside the thread's scroll container, so
     an absolutely positioned panel would be placed against the scrolled CONTENT
     and slide away as the reader scrolls. */
  return (
    <div className="fixed inset-0 z-40 flex justify-end" role="presentation">
      {/* The thread stays visible and dimmed: the log is context for what is
          behind it, not a separate place. */}
      <div
        className="absolute inset-0 bg-[color-mix(in_oklab,var(--ink)_18%,transparent)]"
        onMouseDown={onClose}
        role="presentation"
      />
      <section
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label="Sources"
        tabIndex={-1}
        className="pb-glass-float relative flex h-full w-[min(34rem,calc(100vw-3rem))] flex-col shadow-[var(--shadow-lift)]"
      >
        <div className="flex shrink-0 items-center justify-between gap-3 border-b border-[var(--line)] px-5 py-3">
          <h2 className="text-[14px] font-medium text-[var(--ink)]">
            Sources
            {sources.length > 0 && (
              <span className="ml-2 text-[12px] font-normal text-[var(--ink-3)]">{sources.length}</span>
            )}
          </h2>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            aria-label="Close sources"
            className="inline-flex h-8 w-8 items-center justify-center rounded-[8px] text-[var(--ink-2)] transition-colors duration-150 hover:bg-[var(--surface-2)] hover:text-[var(--ink)] focus-visible:outline-none focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-[var(--accent)]"
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </div>

        {/* The pages consulted, as pages — a flat list of real links, not the
            raw call payloads they were mined from and not wrapped in a second
            card, since the dialog already frames them. */}
        <div className="min-h-0 flex-1 overflow-y-auto">
          {simulated && (
            <p className="border-b border-[var(--line)] px-5 py-2 text-[12px] text-[var(--warn)]">
              Historical synthetic trace
            </p>
          )}
          {sources.length === 0 ? (
            <p className="px-5 py-6 text-[13px] leading-relaxed text-[var(--ink-2)]">
              This step consulted no external pages.
            </p>
          ) : (
            <ul className="divide-y divide-[var(--line)]">
              {sources.map((source) => {
                const href = safeHttpUrl(source.url);
                const title = safeVisibleText(source.title) || safeVisibleText(source.host);
                return (
                  <li key={source.url}>
                    <a
                      href={href || undefined}
                      target="_blank"
                      rel="noreferrer"
                      className="flex flex-col gap-0.5 px-5 py-3 transition-colors duration-150 hover:bg-[var(--surface-2)] focus-visible:bg-[var(--surface-2)] focus-visible:outline-none"
                    >
                      <span className="pb-contain truncate text-[13px] font-medium text-[var(--ink)]">
                        {title}
                      </span>
                      <span className="pb-contain truncate text-[12px] text-[var(--ink-3)]">
                        {safeVisibleText(source.host)}
                      </span>
                    </a>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </section>
    </div>
  );
}
