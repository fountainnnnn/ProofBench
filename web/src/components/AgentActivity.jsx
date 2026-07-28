/* The agent's work, shown the way a conversation shows it.
 *
 * While the run is live this is bare text in the thread — the same register as
 * "Agent is thinking" — because what the agent is doing right now IS the
 * message, and wrapping it in a card made the live work read as an attachment
 * to a reply that had not arrived yet.
 *
 * Once the run settles it collapses to a single sentence ("Searched the web ·
 * 3 searches") that opens the full log in a side panel. The detail is never
 * lost, it just stops competing with the answer.
 */

import { useMemo } from "react";
import { sanitizeForDisplay, safeVisibleText } from "../displaySafety.js";
import { activitySummaryText, callQuery, summarizeTrace, toolVerbs, traceSources } from "../traceSummary.js";
import StatusIcon from "./StatusIcon.jsx";

const statusTone = (status) => (status === "ok" ? "ok" : status === "error" ? "danger" : "running");

/* One search reaches ten pages; listing all of them turns narration back into a
   log. The full set is always one click away in the trace panel. */
const PAGES_PER_CALL = 3;

/** Live: one line per tool, each with its most recent calls under it. */
function LiveActivity({ groups }) {
  return (
    <div className="flex flex-col gap-2.5">
      {groups.map((group) => {
        const words = toolVerbs(group.tool);
        return (
          <div key={group.tool} className="flex flex-col gap-1">
            <p className="flex items-center gap-2 text-[13px] font-medium text-[var(--ink-2)]">
              <span className="shrink-0 text-[var(--accent)]">
                <StatusIcon tone={group.pending > 0 ? "running" : "ok"} size={13} pulse={group.pending > 0} />
              </span>
              {group.pending > 0 ? words.gerund : words.verb}
              <span className="text-[var(--ink-3)]">
                {group.calls} {group.calls === 1 ? words.noun : words.plural}
              </span>
            </p>
            <ul className="flex flex-col gap-0.5 pl-[21px]">
              {group.items.flatMap((call, index) => {
                /* Each row states the action and what it reached, so watching a
                   run reads as narration rather than as a list of addresses.
                   A page is named by its headline with the site beside it: a
                   bare domain says a search touched microsoft.com, which is not
                   the same as telling the reader what it read there. */
                const pending = call.status !== "ok" && call.status !== "error";
                const action = pending ? words.acting : words.act;
                const pages = pending ? [] : traceSources([call]).slice(0, PAGES_PER_CALL);
                const query = callQuery(call);
                const rows = pages.length > 0
                  ? pages.map((page) => ({
                      key: page.url,
                      label: safeVisibleText(page.title) || page.host,
                      site: safeVisibleText(page.title) ? page.host : "",
                    }))
                  /* Nothing came back yet, or the call carried no pages: name
                     what was asked for instead of dropping the row silently. */
                  : [{
                      key: "args",
                      label: safeVisibleText(query || call.args_summary || call.summary || ""),
                      site: "",
                    }];
                return rows
                  .filter((row) => row.label)
                  .map((row) => (
                    <li
                      key={`${group.tool}-${call.index ?? index}-${row.key}`}
                      className="pb-contain flex items-start gap-1.5 text-[12px] leading-relaxed text-[var(--ink-3)]"
                    >
                      <span className="mt-[3px] shrink-0" style={{ color: call.status === "error" ? "var(--danger)" : undefined }}>
                        <StatusIcon tone={statusTone(call.status)} size={11} />
                      </span>
                      <span className="min-w-0 truncate">
                        {action && (
                          <>
                            <span className="text-[var(--ink-2)]">{action}</span>{" "}
                          </>
                        )}
                        {row.label}
                        {row.site && (
                          <span className="text-[var(--ink-3)] opacity-70"> · {row.site}</span>
                        )}
                      </span>
                    </li>
                  ));
              })}
            </ul>
          </div>
        );
      })}
    </div>
  );
}

export default function AgentActivity({ trace, sandboxLogs, phaseState, simulated, live = false, onOpenLog }) {
  const safeTrace = useMemo(() => sanitizeForDisplay(trace || []), [trace]);
  const groups = useMemo(() => summarizeTrace(safeTrace), [safeTrace]);
  const summary = useMemo(() => activitySummaryText(safeTrace), [safeTrace]);
  /* What the reader is actually being offered when they click: the pages this
     step consulted. Leading with that count beats "9 steps", which counts
     internal calls nobody asked about. */
  const sources = useMemo(() => traceSources(safeTrace), [safeTrace]);
  const errors = groups.reduce((n, g) => n + g.errors, 0);

  if (groups.length === 0) return null;

  if (live) {
    return (
      <div className="min-w-0" aria-live="polite">
        <LiveActivity groups={groups} />
        {simulated && (
          <p className="mt-2 text-[12px] text-[var(--warn)]">Historical synthetic trace</p>
        )}
      </div>
    );
  }

  /* Settled: one quiet line. It is a button, not a card, so it sits in the
     thread at the weight of a caption and only becomes the subject when the
     reader chooses to open it. */
  return (
    <button
      type="button"
      onClick={onOpenLog}
      className="group inline-flex max-w-full items-center gap-2 self-start rounded-full px-2.5 py-1 text-[12.5px] text-[var(--ink-2)] transition-colors duration-150 hover:bg-[var(--surface-2)] hover:text-[var(--ink)] focus-visible:outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)]"
    >
      <span className="shrink-0 text-[var(--ink-3)]">
        <StatusIcon tone={errors > 0 ? "danger" : "ok"} size={13} />
      </span>
      <span className="pb-contain min-w-0 truncate">
        {sources.length > 0
          ? `Searched ${sources.length} ${sources.length === 1 ? "site" : "sites"} on the web`
          : summary}
      </span>
      {errors > 0 && (
        <span className="shrink-0 text-[var(--danger)]">
          {errors} {errors === 1 ? "error" : "errors"}
        </span>
      )}
      <svg
        width="13" height="13" viewBox="0 0 16 16" fill="none" aria-hidden="true"
        className="shrink-0 text-[var(--ink-3)] transition-transform duration-150 group-hover:translate-x-0.5"
      >
        <path d="M6 3.5 10.5 8 6 12.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </button>
  );
}
