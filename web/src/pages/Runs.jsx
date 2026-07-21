/* eslint-disable jsx-a11y/no-redundant-roles, jsx-a11y/no-interactive-element-to-noninteractive-role --
   The run table restacks into labelled row summaries at 720px by changing the
   display type of its table elements. That drops the implicit table semantics
   in browsers, so table, rowgroup, row, columnheader, and cell are re-declared
   explicitly. The roles restate what the element already is; they never
   override an interactive element. */
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { listSessions } from "../api.js";
import { safeVisibleText } from "../displaySafety.js";
import { authoritativeProvenance } from "../provenance.js";
import { relativeTime } from "../relativeTime.js";
import { BTN_PRIMARY, INPUT, PAGE_HEADER, PAGE_TITLE, InlineError, PANEL, Skeleton } from "../components/ui.jsx";
import HeaderActions from "../components/HeaderActions.jsx";

const PHASE_BADGE = {
  // --info on a 10% tint of itself lands at 3.5:1 — below the 4.5:1 axe wants
  // for this small label. The accent pair is the design system's existing
  // accessible "informational" surface at 5:1.
  info: "bg-[color:var(--accent-soft)] text-[color:var(--accent)]",
  warn: "bg-[color:var(--warn-tint)] text-[color:var(--warn)]",
  ok: "bg-[color:var(--ok-tint)] text-[color:var(--ok)]",
  // Same problem as `info`: --err on a 10% tint of itself is 4.4:1, just under
  // the 4.5:1 axe wants. --err-strong on --err-soft is the design system's
  // accessible error pair.
  err: "bg-[color:var(--err-soft)] text-[color:var(--err-strong)]",
  neutral: "bg-[color:var(--surface-2)] text-[color:var(--ink-2)]",
};

// The evidence column reports what the backend can actually prove. A session's
// `mode` is deliberately not consulted: it is 'real' for every session ever
// created, including empty, running, failed, and pending ones, so reading it
// would label unproven sessions as measured evidence.
function evidenceBadge(session) {
  const { status } = authoritativeProvenance({ provenance: session.provenance });
  if (status === "measured") return { label: "Measured", tone: "ok" };
  if (status === "synthetic") return { label: "Historical synthetic", tone: "warn" };
  if (status === "unverified") return { label: "Unverified", tone: "err" };
  // pending: distinguish a run that ended badly from one still on its way.
  if (session.latest_run_failed || (session.phase || "").toUpperCase().includes("FAIL")) {
    return { label: "Failed", tone: "err" };
  }
  if (session.is_running) return { label: "Real execution", tone: "info" };
  return { label: "Pending", tone: "neutral" };
}

const BADGE = "inline-flex items-center rounded-full px-2.5 py-0.5 text-[12px] font-medium";

function EvidenceBadge({ session }) {
  const { label, tone } = evidenceBadge(session);
  return <span className={`${BADGE} ${PHASE_BADGE[tone]}`}>{label}</span>;
}

// One pill per row: evidence keeps its badge; phase demotes to a quiet subtitle
// under the session title so the authoritative provenance column is never echoed
// by a second coloured chip, nor by a whole column half-duplicating it.
function PhaseLabel({ phase, evidenceLabel }) {
  const label = safeVisibleText(phase || "unknown").toLowerCase().replace(/_/g, " ");
  // When the phase is what produced the evidence label ("failed" -> "Failed"),
  // the subtitle would print the badge's own word a second time on one row.
  if (label === String(evidenceLabel || "").toLowerCase()) return null;
  return <span className="mt-0.5 block truncate text-[12px] text-[var(--ink-3)]">{label}</span>;
}

// Relative age with the absolute datetime on hover.
function RunTime({ value }) {
  const d = new Date(value);
  const valid = value && !Number.isNaN(d.getTime());
  return (
    <time dateTime={valid ? value : undefined} title={valid ? d.toLocaleString() : undefined}>
      {relativeTime(value)}
    </time>
  );
}

const PHASE_FILTERS = [
  { key: "all", label: "All" },
  { key: "done", label: "Done" },
  { key: "inprogress", label: "In progress" },
  { key: "failed", label: "Failed" },
];

function matchesPhase(phase, filter) {
  const p = (phase || "").toUpperCase();
  if (filter === "done") return p === "DONE";
  if (filter === "failed") return p.includes("FAIL");
  if (filter === "inprogress") return p !== "DONE" && !p.includes("FAIL");
  return true;
}

function ChevronIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path
        d="M6 3.5 10.5 8 6 12.5"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

// Labels float on the white card — no fill, no rule; whitespace separates them
// from the rows.
const TH = "px-4 pb-2.5 pt-4 text-[12px] font-semibold text-[var(--ink-3)]";

export default function Runs() {
  const [sessions, setSessions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [query, setQuery] = useState("");
  const [phaseFilter, setPhaseFilter] = useState("all");

  const load = async () => {
    try {
      const list = await listSessions();
      setSessions(Array.isArray(list) ? list : []);
      setError(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, []);

  const total = sessions.length;
  const done = sessions.filter((s) => (s.phase || "").toUpperCase() === "DONE").length;
  const failed = sessions.filter((s) => (s.phase || "").toUpperCase().includes("FAIL")).length;
  const inProgress = total - done - failed;

  const trimmedQuery = query.trim().toLowerCase();
  const filtersActive = trimmedQuery !== "" || phaseFilter !== "all";
  const visible = useMemo(
    () =>
      sessions.filter((s) => {
        if (trimmedQuery && !safeVisibleText(s.title || "").toLowerCase().includes(trimmedQuery)) {
          return false;
        }
        return matchesPhase(s.phase, phaseFilter);
      }),
    [sessions, trimmedQuery, phaseFilter],
  );

  const clearFilters = () => {
    setQuery("");
    setPhaseFilter("all");
  };

  const rememberSession = (id) => localStorage.setItem("proofbench.activeSessionId", id);

  return (
    <div className="flex min-h-full flex-col">
      <header className={`${PAGE_HEADER} px-4 sm:px-8`}>
        <div className="mx-auto flex w-full max-w-canvas flex-wrap items-end justify-between gap-x-6 gap-y-1 pb-3 pt-3.5">
          <div className="min-w-0">
            <h1 className={PAGE_TITLE}>Runs</h1>
            <p className="mt-0.5 text-[13px] text-[var(--ink-2)]">
              Every benchmark session on this deployment, with the evidence each one can prove.
            </p>
          </div>
          {/* The run counts live with the runs, in the table card's own header
              row, not up here as a second reading of the same list. */}
          <HeaderActions />
        </div>
      </header>

      <div className="mx-auto w-full max-w-canvas px-4 pb-12 sm:px-8">
        {error && (
          <div className="mt-6">
            <InlineError onRetry={load}>Could not load sessions: {error}</InlineError>
          </div>
        )}

        <div className={`mt-6 ${PANEL} overflow-hidden`}>
        {!loading && total > 0 && (
          <div className="flex flex-wrap items-center gap-2 border-b border-[var(--line)] px-4 py-3">
            <span className="relative max-w-[280px] flex-1">
              <svg
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
                aria-hidden="true"
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[var(--ink-3)]"
              >
                <circle cx="11" cy="11" r="7" />
                <path d="m20 20-3.2-3.2" />
              </svg>
              <input
                type="search"
                aria-label="Search runs"
                placeholder="Search runs"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                className={`${INPUT.replace("h-10", "h-9").replace("px-3.5", "pl-9 pr-3.5")} text-[13px]`}
              />
            </span>
            {PHASE_FILTERS.map(({ key, label }) => {
              const active = phaseFilter === key;
              return (
                <button
                  key={key}
                  type="button"
                  aria-pressed={active}
                  onClick={() => setPhaseFilter(key)}
                  className={`min-h-8 rounded-full px-3 text-[12px] font-medium transition-colors duration-150 ease-out-quart ${
                    active
                      ? "bg-[var(--ink)] text-[var(--surface)]"
                      : "bg-[var(--surface-2)] text-[var(--ink-2)] hover:text-[var(--ink)]"
                  }`}
                >
                  {label}
                </button>
              );
            })}
            {/* Astryx's table pattern: the live result count belongs in the
                table's own header, beside the control that changes it. */}
            <span className="ml-auto shrink-0 text-[12px] text-[var(--ink-2)]">
              <span className="pb-mono font-medium text-[var(--ink)]">
                {filtersActive ? `${visible.length} of ${total}` : total}
              </span>{" "}
              {filtersActive ? "shown" : total === 1 ? "run" : "runs"}
            </span>
          </div>
        )}

        {loading ? (
          <div className="flex flex-col gap-3 p-5" role="status" aria-label="Loading runs">
            {[...Array(5)].map((_, i) => (
              <div key={i} className="flex items-center gap-4">
                <Skeleton className="h-4 w-1/3" />
                <Skeleton className="h-4 w-20" />
                <Skeleton className="h-4 w-24" />
                <Skeleton className="ml-auto h-4 w-28" />
              </div>
            ))}
          </div>
        ) : sessions.length === 0 ? (
          <div className="flex flex-col items-start gap-3 px-4 py-10 sm:px-6">
            <p className="text-[14px] text-[var(--ink)]">No runs on this deployment yet.</p>
            <p className="max-w-[60ch] text-[13px] text-[var(--ink-2)]">
              Start a benchmark and the session appears here as soon as it is created, with its
              phase and what its results can prove.
            </p>
            <Link to="/app/benchmark" className={BTN_PRIMARY}>
              Start a benchmark
            </Link>
          </div>
        ) : visible.length === 0 ? (
          <div className="px-6 py-10">
            <p className="text-[14px] text-[var(--ink)]">No runs match.</p>
            <p className="mt-1 text-[13px] text-[var(--ink-2)]">
              Adjust the search or clear the filter.
            </p>
            <button
              type="button"
              onClick={clearFilters}
              className="mt-3 inline-flex min-h-9 items-center text-[13px] font-medium text-[var(--accent)]"
            >
              Clear filters
            </button>
          </div>
        ) : (
          <table role="table" className="pb-stack-table text-left text-[13px]">
            <caption className="sr-only">
              Benchmark sessions with phase, evidence status, and creation time
            </caption>
            <thead role="rowgroup">
              <tr role="row">
                {/* The session column absorbs the free width so a long title
                    truncates only when it genuinely runs out of room. */}
                <th role="columnheader" scope="col" className={`${TH} w-full`}>Session</th>
                <th role="columnheader" scope="col" className={`${TH} whitespace-nowrap`}>Evidence</th>
                <th role="columnheader" scope="col" className={`${TH} whitespace-nowrap`}>Created</th>
                <th role="columnheader" scope="col" className={`${TH} w-12 text-right`}>
                  <span className="sr-only">Open</span>
                </th>
              </tr>
            </thead>
            <tbody role="rowgroup">
              {visible.map((s) => (
                <tr
                  role="row"
                  key={s.id}
                  className="border-b border-[var(--line)] transition-colors duration-150 ease-out-quart last:border-b-0 hover:bg-[var(--surface-2)] focus-within:bg-[var(--surface-2)]"
                >
                  <td role="cell" data-primary="" className="px-4 py-3 md:w-full md:max-w-0">
                    <Link
                      to={`/app/benchmark?session=${encodeURIComponent(s.id)}`}
                      onClick={(event) => {
                        event.stopPropagation();
                        rememberSession(s.id);
                      }}
                      title={safeVisibleText(s.title || "Untitled")}
                      className="pb-contain block rounded-[12px] font-medium text-[var(--ink)] transition-colors duration-150 ease-out-quart hover:text-[var(--accent)] md:line-clamp-2"
                    >
                      {safeVisibleText(s.title || "Untitled")}
                    </Link>
                    <PhaseLabel phase={s.phase} evidenceLabel={evidenceBadge(s).label} />
                  </td>
                  <td role="cell" data-label="Evidence" className="px-4 py-3">
                    <EvidenceBadge session={s} />
                  </td>
                  <td
                    role="cell"
                    data-label="Created"
                    className="pb-mono whitespace-nowrap px-4 py-3 text-[12px] text-[var(--ink-2)]"
                  >
                    <RunTime value={s.created_at} />
                  </td>
                  <td role="cell" data-actions="" className="px-4 py-3 text-right">
                    <Link
                      to={`/app/benchmark?session=${encodeURIComponent(s.id)}`}
                      title="Open in the benchmark console"
                      onClick={(event) => {
                        event.stopPropagation();
                        rememberSession(s.id);
                      }}
                      className="inline-flex min-h-10 items-center gap-1.5 rounded-[12px] px-2 text-[13px] text-[var(--ink-2)] transition-colors duration-150 ease-out-quart hover:text-[var(--accent)] md:min-h-8 md:px-1"
                    >
                      <span className="md:sr-only">Open run</span>
                      <ChevronIcon />
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        </div>
      </div>
    </div>
  );
}
