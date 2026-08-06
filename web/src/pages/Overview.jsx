import { useEffect, useMemo, useReducer, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { fetchBrandLogos, getResults, listSessions } from "../api.js";
import { safeVisibleText, sanitizeForDisplay } from "../displaySafety.js";
import { authoritativeProvenance, canRenderMetrics } from "../provenance.js";
import { relativeTime } from "../relativeTime.js";
import { buildCanonicalRows, buildDecision, candidateLabel, componentRows, primaryMetricKey } from "../resultsModel.js";
import { BTN_PRIMARY, InlineError, PAGE_HEADER, PAGE_TITLE, PANEL, Skeleton, useFitList } from "../components/ui.jsx";
import HeaderActions from "../components/HeaderActions.jsx";
import StatusIcon from "../components/StatusIcon.jsx";
import { ContributionCalendar, RankBars } from "../components/charts.jsx";
import { brandAssetFor, ensureBrandAssets, runtimeBrandAssetFor } from "../brandIcons.js";
import { selectResumeSession } from "../overviewResume.js";

/* This page answers the one question no other page does: what did the work
   actually conclude. Runs owns the session list, Datasets owns the data,
   Settings owns provider configuration, so none of that is repeated here.
   What lives here is the verdict of each finished benchmark and the roll-up of
   every tool those benchmarks have judged. */

const METRIC_LABEL = {
  exact_accuracy: "Exact acc.",
  rating: "Rating",
};

/* Hue by evidence type, the one distinction this product actually turns on.
   Coral, the brand accent, marks a number that was measured by executing the
   tool; stone marks one merely rated from documentation. The palette has no
   second chroma to spend, and the pairing says the right thing anyway: the
   real evidence is the vivid one, the inert evidence is the neutral one. */
const METRIC_HUE = {
  exact_accuracy: "var(--accent)",
  rating: "var(--stone)",
};

const METRIC_GROUP = {
  exact_accuracy: "Measured by execution",
  rating: "Rated from documentation",
};

const METRIC_GROUP_ORDER = {
  exact_accuracy: 0,
  rating: 1,
};

function formatMetric(key, value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return null;
  if (key === "rating") return `${Math.round(Number(value))}/100`;
  return `${(Number(value) * 100).toFixed(1)}%`;
}

function ArrowIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true" className="shrink-0">
      <path d="M6 3.5 10.5 8 6 12.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function SectionCard({ title, count, children, grow = false, action = null }) {
  return (
    <section
      className={`${PANEL} flex flex-col overflow-hidden ${grow ? "min-h-0 flex-1" : ""}`}
      aria-label={title}
    >
      <div
        className="flex items-center justify-between gap-3 pb-1"
        style={{
          paddingLeft: "var(--space-card-x)",
          paddingRight: "var(--space-card-x)",
          paddingTop: "calc(var(--space-card-y) * 1.6)",
        }}
      >
        <h2 className="text-[16px] font-semibold tracking-[-0.01em] text-[var(--ink)]">{title}</h2>
        <div className="flex shrink-0 items-center gap-2">
          {count !== null && count !== undefined && (
            <span className="pb-mono text-[12px] text-[var(--ink-3)]">{count}</span>
          )}
          {action}
        </div>
      </div>
      {children}
    </section>
  );
}

/* The margin behind a verdict, stated rather than plotted. "X won" is a claim;
   the number a reader actually needs is how far clear it finished, which is one
   subtraction — so it is written out instead of left to be eyeballed off five
   bars. The beaten field follows as compact chips, ordered as they ranked. */
function VerdictMargin({ item }) {
  const rows = [...item.rows].sort(
    (a, b) => (Number(b[item.metricKey]) || -1) - (Number(a[item.metricKey]) || -1),
  );
  const [winner, runnerUp] = rows;
  const top = Number(winner?.[item.metricKey]);
  const next = Number(runnerUp?.[item.metricKey]);
  const gap = Number.isFinite(top) && Number.isFinite(next) ? top - next : null;
  const isRating = item.metricKey === "rating";
  const gapLabel = gap === null
    ? null
    : gap === 0
      ? "Tied on the primary metric"
      : `+${isRating ? Math.round(gap) : `${(gap * 100).toFixed(1)} pts`}${isRating ? "" : ""} clear of ${safeVisibleText(candidateLabel(runnerUp))}`;

  /* A sole candidate has no margin to report. Saying so in one line is honest
     and costs a row's height; rendering nothing left an empty band instead. */
  if (rows.length <= 1) {
    return (
      <div style={{ paddingLeft: "var(--space-card-x)", paddingRight: "var(--space-card-x)", paddingBottom: "calc(var(--space-card-y) * 1.3)" }}>
        <p className="flex items-center gap-1.5 text-[12px] text-[var(--ink-3)]">
          <span aria-hidden="true" className="h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--ink-3)]" />
          Only candidate in this benchmark — no comparison to draw.
        </p>
      </div>
    );
  }

  return (
    <div className="px-5 pb-3.5">
      {gapLabel && (
        <p className="flex items-center gap-1.5 text-[12px] font-medium text-[var(--ink-2)]">
          <span
            aria-hidden="true"
            className="h-1.5 w-1.5 shrink-0 rounded-full"
            style={{ backgroundColor: gap === 0 ? "var(--warn)" : "var(--ok)" }}
          />
          {gapLabel}
        </p>
      )}
      {rows.length > 1 && (
        <ul className="mt-2 flex flex-wrap gap-1.5">
          {rows.slice(1).map((row) => {
            const value = formatMetric(item.metricKey, row[item.metricKey]);
            return (
              <li
                key={row.name}
                className="inline-flex min-w-0 max-w-full items-center gap-1.5 rounded-full bg-[var(--surface-2)] py-1 pl-1.5 pr-2.5"
                title={candidateLabel(row)}
              >
                <BrandIcon name={row.name} size={16} />
                <span className="pb-contain min-w-0 truncate text-[12px] text-[var(--ink-2)]">
                  {safeVisibleText(candidateLabel(row))}
                </span>
                {value && (
                  <span className="pb-mono shrink-0 text-[11px] text-[var(--ink-3)]">{value}</span>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

/* One finished benchmark, read as its conclusion rather than its status.
   Rows are content-sized, never stretched: a fixed tall row plus flex-1 made a
   verdict with one runner-up as tall as one with five, which is mostly air, and
   the fit-list then showed fewer verdicts than would actually fit. */
function VerdictRow({ item, pad = 0 }) {
  const score = formatMetric(item.metricKey, item.winner[item.metricKey]);
  return (
    <li
      data-fit-item
      className="flex flex-col"
      style={pad ? { paddingBottom: pad } : undefined}
    >
      <Link
        to={`/app/benchmark?session=${encodeURIComponent(item.sessionId)}`}
        onClick={() => localStorage.setItem("proofbench.activeSessionId", item.sessionId)}
        className="flex items-center gap-3.5 transition-colors duration-150 hover:bg-[var(--surface-2)] focus-visible:bg-[var(--surface-2)] focus-visible:outline-none"
        style={{
          paddingLeft: "var(--space-card-x)",
          paddingRight: "var(--space-card-x)",
          paddingTop: "calc(var(--space-card-y) * 1.4)",
          paddingBottom: "calc(var(--space-card-y) * 0.8)",
        }}
      >
        <BrandIcon name={item.winner.name} size={30} />
        <span className="min-w-0 flex-1">
          <span className="pb-contain block truncate text-[16px] font-semibold tracking-[-0.01em] text-[var(--ink)]">
            {/* A run where nothing cleared the bar has no winner to name, so the
                row states the outcome rather than promoting the top scorer. */}
            {item.unmet
              ? "No candidate met the requirements"
              : safeVisibleText(candidateLabel(item.winner))}
          </span>
          <span className="pb-contain mt-1 block truncate text-[12px] text-[var(--ink-2)]">
            {item.unmet
              ? `${item.failedCount} of ${item.rankedCount} rated not implementable`
              : `won ${item.rankedCount === 1 ? "the only entry" : `over ${item.rankedCount - 1} other${item.rankedCount === 2 ? "" : "s"}`}`}
            {" in "}
            {safeVisibleText(item.title)}
          </span>
        </span>
        {/* The verdict's number is the headline of this card, so it is set at
            display size — the leaderboard beside it keeps its numbers small
            because there the bar carries the comparison, not the digits. */}
        {score && (
          <span className="shrink-0 text-right">
            <span className="block text-[10px] font-medium uppercase tracking-[0.06em] text-[var(--ink-3)]">
              {METRIC_LABEL[item.metricKey] || "Score"}
            </span>
            <span className="pb-mono mt-0.5 block text-[19px] font-medium leading-none tracking-[-0.02em] text-[var(--ink)]">
              {score}
            </span>
          </span>
        )}
        <time
          className="pb-mono hidden shrink-0 text-[12px] text-[var(--ink-3)] sm:block"
          dateTime={item.createdAt}
          title={new Date(item.createdAt).toLocaleString()}
        >
          {relativeTime(item.createdAt)}
        </time>
        <span className="shrink-0 text-[var(--ink-3)]">
          <ArrowIcon />
        </span>
      </Link>
      {/* Deliberately NOT the leaderboard's bar list — the card beside this one
          already ranks tools that way, and two identical treatments make the
          page read as one repeated widget. A verdict answers a different
          question: was this decisive, and who did it beat. So it states the
          margin in words and shows the beaten field as compact chips. */}
      <VerdictMargin item={item} />
    </li>
  );
}

/* Every candidate these benchmarks have judged, and how it did at its best.
   Assembled across runs, so it exists NOWHERE else in the product — which is
   exactly why this list must show every tool rather than truncate to a "see
   all" link: there is no other page for that link to lead to. It reads in two
   columns so the whole set fits the card at a dashboard's density. */
/* Known third-party services use bundled brand marks. Custom benchmark
   adapters keep the neutral monogram, so every row remains aligned and the
   dashboard never makes a runtime request to an icon service. */
function BrandIcon({ name, size = 22 }) {
  const [failed, setFailed] = useState(false);
  const asset = brandAssetFor(name) || runtimeBrandAssetFor(name);
  const initial = (String(name || "?").trim()[0] || "?").toUpperCase();
  const box = { width: size, height: size };

  if (failed || !asset) {
    return (
      <span
        aria-hidden="true"
        style={box}
        className="flex shrink-0 items-center justify-center rounded-[7px] bg-[var(--accent-tint)] text-[10px] font-semibold uppercase text-[var(--accent)]"
      >
        {initial}
      </span>
    );
  }
  return (
    <span
      aria-hidden="true"
      style={box}
      className="flex shrink-0 items-center justify-center rounded-[7px] bg-[var(--surface-2)] p-[3px]"
    >
      <img
        src={asset}
        alt=""
        width={size - 6}
        height={size - 6}
        onError={() => setFailed(true)}
        className="h-full w-full object-contain"
      />
    </span>
  );
}

/* The row's legibility floor: one line plus its padding. A minimum, not a fixed
   height — the group scrolls when its rows exceed the card, so a row is never
   compressed below this to force a fit. */
const TOOL_ROW_MIN = 26;

/* One evidence group as a ranked leaderboard: sorted best-first, each tool's
   score drawn as a bar so the standing is read at a glance rather than by
   comparing numbers. The leader's bar carries the group's full hue, the rest a
   muted step of it, and a win is marked with the same check used everywhere
   else.

   The group does not clip its own rows: every group lives inside one shared
   scroll region (see the Tools card and the sheet), so a group taller than the
   space it was handed is reached by scrolling, not truncated. Its header is
   sticky, so the scale it names stays visible while its rows scroll under it. */
function ToolLeaderboard({ metricKey, tools }) {
  const sorted = [...tools].sort((a, b) => (Number(b.best) || -1) - (Number(a.best) || -1));
  const max = metricKey === "rating" ? 100 : Math.max(1, ...sorted.map((t) => Number(t.best) || 0));
  const hue = METRIC_HUE[metricKey] || "var(--accent)";

  return (
    <div className="flex flex-col">
      <ToolGroupHeader metricKey={metricKey} />
      <ul className="flex flex-col">
        {sorted.map((tool, index) => {
          const value = Number(tool.best);
          const finite = Number.isFinite(value);
          const pct = finite && max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
          const isLeader = index === 0 && finite && value > 0;
          const score = formatMetric(metricKey, tool.best);
          return (
            /* Rows hold a legibility floor of one line plus its padding and are
               never compressed below it: the shared region scrolls instead. */
            <li
              key={tool.name}
              className="flex items-center gap-2.5 overflow-hidden border-t border-[var(--line)]"
              style={{
                minHeight: TOOL_ROW_MIN,
                paddingLeft: "var(--space-card-x)",
                paddingRight: "var(--space-card-x)",
                paddingTop: "calc(var(--space-card-y) * 0.55)",
                paddingBottom: "calc(var(--space-card-y) * 0.55)",
              }}
            >
              <BrandIcon name={tool.name} size={20} />
              <span
                className="pb-contain min-w-0 flex-[1.5] truncate text-[13px] text-[var(--ink)]"
                title={candidateLabel(tool)}
              >
                {safeVisibleText(candidateLabel(tool))}
              </span>
              <span className="relative hidden h-1.5 flex-[2] overflow-hidden rounded-full bg-[var(--surface-2)] sm:block">
                <span
                  className="absolute inset-y-0 left-0 rounded-full"
                  style={{
                    width: `${pct}%`,
                    backgroundColor: isLeader ? hue : `color-mix(in oklab, ${hue} 42%, var(--surface-2))`,
                  }}
                />
              </span>
              {tool.wins > 0 && (
                <span className="shrink-0 text-[var(--ok)]" title="Won a benchmark" aria-label="Won a benchmark">
                  <StatusIcon tone="ok" size={13} />
                </span>
              )}
              <span className="pb-mono w-[7ch] shrink-0 text-right text-[12px] text-[var(--ink-2)]">
                {score ?? "n/a"}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/* Tools split by how their number was arrived at, because the two groups are
   not comparable. The header names the scale once instead of every row
   repeating it, and carries the group's hue as a small key. */
function ToolGroupHeader({ metricKey }) {
  return (
    <div
      className="sticky top-0 z-10 flex items-center gap-2 bg-[var(--surface-2)] py-1.5"
      style={{ paddingLeft: "var(--space-card-x)", paddingRight: "var(--space-card-x)" }}
    >
      <span
        aria-hidden="true"
        className="h-2 w-2 rounded-[3px]"
        style={{ backgroundColor: METRIC_HUE[metricKey] || "var(--accent)" }}
      />
      <span className="text-[11px] font-medium text-[var(--ink-2)]">
        {METRIC_GROUP[metricKey] || "Scored"}
      </span>
      <span className="pb-mono ml-auto text-[11px] text-[var(--ink-3)]">
        {METRIC_LABEL[metricKey]}
      </span>
    </div>
  );
}

/* Every evidence group, stacked. Shared verbatim between the dashboard card and
   the expanded sheet so the two can never rank a tool differently or split a
   group by evidence type in one place but not the other. */
function ToolGroups({ groups }) {
  return groups.map(([metricKey, group]) => (
    <ToolLeaderboard key={metricKey} metricKey={metricKey} tools={group} />
  ));
}

function ExpandIcon() {
  return (
    <svg
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M9 4H5a1 1 0 0 0-1 1v4M15 4h4a1 1 0 0 1 1 1v4M9 20H5a1 1 0 0 1-1-1v-4M15 20h4a1 1 0 0 0 1-1v-4" />
    </svg>
  );
}

/* The full leaderboard, in a side sheet. The dashboard card shows as much of the
   ranking as the viewport allows and scrolls the rest; this opens the same list
   at full height beside the page, so every tool is reachable without the card
   having to own the whole viewport. Modeled on the trace panel: it dims the page
   rather than replacing it, closes on the backdrop or Escape, traps focus while
   open, and restores focus to the control that opened it. Near full width under
   its own breakpoint so it stays usable at 390px. */
function ToolsSheet({ open, onClose, groups, count }) {
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

  return (
    <div className="fixed inset-0 z-40 flex justify-end" role="presentation">
      <div
        className="absolute inset-0 bg-[color-mix(in_oklab,var(--ink)_18%,transparent)]"
        onMouseDown={onClose}
        role="presentation"
      />
      <section
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label="Tools evaluated"
        tabIndex={-1}
        className="pb-glass-float relative flex h-full w-[min(34rem,calc(100vw-3rem))] flex-col shadow-[var(--shadow-lift)]"
      >
        <div className="flex shrink-0 items-center justify-between gap-3 border-b border-[var(--line)] px-5 py-3">
          <h2 className="text-[14px] font-medium text-[var(--ink)]">
            Tools evaluated
            {count > 0 && (
              <span className="ml-2 text-[12px] font-normal text-[var(--ink-3)]">{count}</span>
            )}
          </h2>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            aria-label="Close tools evaluated"
            className="inline-flex h-8 w-8 items-center justify-center rounded-[8px] text-[var(--ink-2)] transition-colors duration-150 hover:bg-[var(--surface-2)] hover:text-[var(--ink)] focus-visible:outline-none focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-[var(--accent)]"
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </div>

        {/* One scroll region for the whole list, with sticky evidence headers
            and no per-group clipping. */}
        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
          <ToolGroups groups={groups} />
        </div>
      </section>
    </div>
  );
}

/** Footer that stands in for verdicts that did not fit. Verdicts alone earns a
    link: a verdict IS a completed run, so Runs is where the rest actually live
    and can be opened. (Tools have no such page, so that list never truncates.) */
function MoreLink({ hidden, noun, footerRef }) {
  if (hidden <= 0) return null;
  return (
    <Link
      ref={footerRef}
      to="/app/runs"
      className="mt-auto flex shrink-0 items-center justify-center gap-1.5 border-t border-[var(--line)] py-2.5 text-[12px] font-medium text-[var(--ink-2)] transition-colors duration-150 hover:bg-[var(--surface-2)] hover:text-[var(--ink)]"
      style={{ paddingLeft: "var(--space-card-x)", paddingRight: "var(--space-card-x)" }}
    >
      {hidden} more {hidden === 1 ? noun : `${noun}s`}
      <ArrowIcon />
    </Link>
  );
}

export default function Overview() {
  const [sessions, setSessions] = useState([]);
  const [verdicts, setVerdicts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const list = await listSessions();
        if (!alive) return;
        const all = Array.isArray(list) ? list : [];
        setSessions(all);
        setError(null);

        // Only a run whose provenance is conclusive can state a verdict. The
        // same guard the results card uses, so the two can never disagree.
        const settled = all.filter((s) => {
          const { status } = authoritativeProvenance({ provenance: s.provenance });
          return s.latest_run_id && canRenderMetrics({ status });
        });
        const resolved = await Promise.all(
          settled.map(async (s) => {
            const data = await getResults(s.latest_run_id).catch(() => null);
            const metrics = sanitizeForDisplay(data?.metrics || null);
            if (!metrics) return null;
            const isAssessment = Object.values(metrics).some((row) => row?.rating !== undefined);
            // Products only. Libraries are parts of a self-built design, never
            // tools that were evaluated, so they never reach the leaderboard.
            const rows = buildCanonicalRows(metrics, isAssessment);
            const decision = buildDecision(rows, isAssessment,
              componentRows(metrics, isAssessment));
            if (!decision?.winner) return null;
            return {
              sessionId: s.id,
              title: s.title || "Untitled benchmark",
              createdAt: s.created_at,
              metricKey: primaryMetricKey(isAssessment),
              winner: decision.winner,
              rankedCount: decision.rankedCount,
              unmet: decision.unmet,
              failedCount: decision.failedCount,
              rows,
              isAssessment,
            };
          }),
        );
        if (!alive) return;
        setVerdicts(resolved.filter(Boolean).sort((a, b) => new Date(b.createdAt) - new Date(a.createdAt)));
      } catch (e) {
        if (alive) setError(e.message);
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, []);

  // One tool can appear in many benchmarks; keep its best primary metric and
  // count the times it came first.
  const tools = useMemo(() => {
    const byName = new Map();
    for (const v of verdicts) {
      for (const row of v.rows) {
        const name = String(row.name || "");
        if (!name) continue;
        const value = Number(row[v.metricKey]);
        const entry = byName.get(name)
          || { name, display_name: row.display_name, appearances: 0, wins: 0, best: null, metricKey: v.metricKey };
        entry.appearances += 1;
        if (row.canonicalRank === 1) entry.wins += 1;
        if (Number.isFinite(value) && (entry.best === null || value > entry.best)) entry.best = value;
        byName.set(name, entry);
      }
    }
    const all = [...byName.values()].sort((a, b) => b.wins - a.wins || b.best - a.best);
    const groups = new Map();
    for (const tool of all) {
      if (!groups.has(tool.metricKey)) groups.set(tool.metricKey, []);
      groups.get(tool.metricKey).push(tool);
    }
    const orderedGroups = [...groups.entries()].sort(
      ([left], [right]) =>
        (METRIC_GROUP_ORDER[left] ?? Number.MAX_SAFE_INTEGER)
        - (METRIC_GROUP_ORDER[right] ?? Number.MAX_SAFE_INTEGER),
    );
    return { all, groups: orderedGroups };
  }, [verdicts]);

  /* Marks for anything the bundle does not already carry. The build-time
     manifest is frozen when the frontend is built, so without this every tool
     benchmarked since the last deploy renders as a monogram forever. */
  const [, redrawIcons] = useReducer((n) => n + 1, 0);
  const iconNames = useMemo(() => tools.all.map((tool) => tool.name), [tools]);
  useEffect(() => {
    let alive = true;
    ensureBrandAssets(iconNames, fetchBrandLogos)
      .then((added) => { if (alive && added) redrawIcons(); });
    return () => { alive = false; };
  }, [iconNames]);

  /* A year of days, week-aligned, for the calendar. Columns are weeks, so the
     window has to end on a Saturday and be a whole multiple of seven or every
     column after a gap would be off by a row. The chart renders only as many of
     the trailing weeks as its width allows; building the full year here keeps
     the date arithmetic in one place and out of the layout. */
  const activity = useMemo(() => {
    const WEEKS = 53;
    const end = new Date();
    end.setHours(0, 0, 0, 0);
    end.setDate(end.getDate() + (6 - end.getDay())); // forward to Saturday

    const days = [];
    for (let i = WEEKS * 7 - 1; i >= 0; i -= 1) {
      const d = new Date(end);
      d.setDate(d.getDate() - i);
      days.push({
        key: d.toISOString().slice(0, 10),
        date: d,
        label: d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }),
        count: 0,
      });
    }
    const index = new Map(days.map((d) => [d.key, d]));
    for (const s of sessions) {
      const key = String(s.created_at || "").slice(0, 10);
      const bucket = index.get(key);
      if (bucket) bucket.count += 1;
    }
    return days;
  }, [sessions]);

  const resume = useMemo(() => selectResumeSession(sessions), [sessions]);
  const nothingYet = !loading && sessions.length === 0;

  // Verdicts fit to the height they are handed; the overflow is a link to Runs,
  // where those completed runs actually live. Tools never truncate — they scroll
  // inside the card and open in full in a side sheet, since Overview is their
  // only home.
  const verdictFit = useFitList(verdicts.length);

  // The Tools card shows as much of the leaderboard as fits and scrolls the
  // rest; this opens the whole list at full height in a side sheet.
  const [toolsOpen, setToolsOpen] = useState(false);

  /* The page is a dashboard, so it fits the viewport rather than scrolling: a
     roll-up you have to scroll is a list. Height flows header -> content, the
     two lists reformat to fit their cards, and only below `lg` — where the
     grid stacks into one column and genuinely cannot fit — does the page itself
     scroll. */
  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className={`${PAGE_HEADER} px-4 sm:px-8`}>
        <div className="mx-auto flex w-full max-w-canvas items-start justify-between gap-x-6 pb-3 pt-3.5">
          <div className="min-w-0">
            <span className="pb-eyebrow-glow">Dashboard</span>
            <h1 className={`${PAGE_TITLE} mt-1`}>Overview</h1>
            <p className="mt-0.5 text-[13px] text-[var(--ink-2)]">
              What this deployment has benchmarked, and what it concluded.
            </p>
          </div>
          <HeaderActions>
            <Link to="/app/benchmark" className={`${BTN_PRIMARY} shrink-0`}>
              New benchmark
            </Link>
          </HeaderActions>
        </div>
      </header>

      {/* Spacing scales with the viewport (see the --space-* tokens): a short
          window spends its pixels on content instead of margin, a tall one
          breathes. */}
      <div
        className="mx-auto flex w-full min-h-0 max-w-canvas flex-1 flex-col overflow-y-auto lg:overflow-hidden"
        style={{
          gap: "var(--space-gap)",
          paddingLeft: "var(--space-page-x)",
          paddingRight: "var(--space-page-x)",
          paddingTop: "var(--space-page-y)",
          paddingBottom: "var(--space-page-y)",
        }}
      >
        {error && <InlineError>Could not load your activity: {error}</InlineError>}

        {loading && (
          <div className={`${PANEL} flex flex-col gap-4 p-5`} role="status" aria-label="Loading overview">
            {[0, 1, 2].map((i) => (
              <div key={i} className="flex items-center gap-4">
                <Skeleton className="h-4 w-48" />
                <Skeleton className="ml-auto h-4 w-16" />
              </div>
            ))}
          </div>
        )}

        {nothingYet && (
          <section className={`${PANEL} px-5 py-10 text-center`} aria-label="Get started">
            <h2 className="pb-display text-[26px] leading-tight text-[var(--ink)]">
              Nothing benchmarked yet
            </h2>
            <p className="mx-auto mt-2 max-w-[52ch] text-[14px] leading-relaxed text-[var(--ink-2)]">
              Describe the tools you want compared and attach a labelled dataset. Every verdict you
              produce, and the evidence behind it, collects here.
            </p>
            <Link to="/app/benchmark" className={`${BTN_PRIMARY} mt-6`}>
              Run your first benchmark
            </Link>
          </section>
        )}

        {!loading && !nothingYet && (
          <>
            {resume && (
              <Link
                to={`/app/benchmark?session=${encodeURIComponent(resume.id)}`}
                onClick={() => localStorage.setItem("proofbench.activeSessionId", resume.id)}
                className={`${PANEL} flex shrink-0 items-center gap-3 transition-colors duration-150 hover:bg-[var(--surface-2)]`}
                style={{
                  paddingLeft: "var(--space-card-x)",
                  paddingRight: "var(--space-card-x)",
                  paddingTop: "calc(var(--space-card-y) * 1.1)",
                  paddingBottom: "calc(var(--space-card-y) * 1.1)",
                }}
              >
                <span className="min-w-0 flex-1">
                  <span className="block text-[12px] text-[var(--ink-2)]">
                    {resume.is_running ? "Running now" : "Pick up where you left off"}
                  </span>
                  <span className="pb-contain mt-0.5 block truncate text-[14px] font-medium text-[var(--ink)]">
                    {safeVisibleText(resume.title || "Untitled benchmark")}
                  </span>
                </span>
                {resume.is_running && (
                  <span className="inline-flex shrink-0 items-center gap-1.5 rounded-full bg-[var(--accent-tint)] px-2.5 py-0.5 text-[12px] font-medium text-[var(--accent)]">
                    <StatusIcon tone="running" size={12} pulse />
                    live
                  </span>
                )}
                <span className="shrink-0 text-[var(--ink-3)]"><ArrowIcon /></span>
              </Link>
            )}

            <div className="shrink-0">
              <SectionCard title="Activity">
                <div
                  className="pt-1"
                  style={{
                    paddingLeft: "var(--space-card-x)",
                    paddingRight: "var(--space-card-x)",
                    paddingBottom: "calc(var(--space-card-y) * 1.5)",
                  }}
                >
                  <p className="mb-2.5 text-[12px] text-[var(--ink-2)]">Benchmarks started</p>
                  <ContributionCalendar days={activity} />
                </div>
              </SectionCard>
            </div>

            <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-12" style={{ gap: "var(--space-gap)" }}>
            <div className="flex min-h-0 flex-col lg:col-span-7">
            <SectionCard title="Verdicts" count={verdicts.length} grow>
              {verdicts.length === 0 ? (
                <div className="px-5 pb-5 pt-1">
                  <p className="text-[13px] text-[var(--ink)]">No benchmark has produced measured evidence yet.</p>
                  <p className="mt-1 max-w-[62ch] text-[13px] leading-relaxed text-[var(--ink-2)]">
                    A verdict appears once a run finishes and its results carry conclusive
                    provenance. Runs that failed or are still awaiting confirmation stay out of this
                    list rather than being reported as findings.{" "}
                    <Link to="/app/runs" className="font-medium text-[var(--accent)] hover:underline">
                      See all runs
                    </Link>
                  </p>
                </div>
              ) : (
                <div ref={verdictFit.outerRef} className="flex min-h-0 flex-1 flex-col border-t border-[var(--line)]">
                  <ul
                    ref={verdictFit.listRef}
                    style={{ maxHeight: verdictFit.maxHeight }}
                    className="flex min-h-0 flex-1 flex-col divide-y divide-[var(--line)] overflow-hidden"
                  >
                    {verdicts.map((item) => (
                      <VerdictRow key={item.sessionId} item={item} pad={verdictFit.pad} />
                    ))}
                  </ul>
                  <MoreLink hidden={verdictFit.hidden} noun="verdict" footerRef={verdictFit.footerRef} />
                </div>
              )}
            </SectionCard>

            </div>

            {tools.all.length > 0 && (
              <div className="flex min-h-0 flex-col lg:col-span-5">
                <SectionCard
                  title="Tools evaluated"
                  count={tools.all.length}
                  grow
                  action={
                    <button
                      type="button"
                      onClick={() => setToolsOpen(true)}
                      aria-haspopup="dialog"
                      aria-expanded={toolsOpen}
                      aria-label="Open the full leaderboard"
                      title="Open the full leaderboard"
                      className="inline-flex h-8 w-8 items-center justify-center rounded-[8px] text-[var(--ink-2)] transition-colors duration-150 hover:bg-[var(--surface-2)] hover:text-[var(--ink)] focus-visible:outline-none focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-[var(--accent)]"
                    >
                      <ExpandIcon />
                    </button>
                  }
                >
                  {/* A leaderboard, not a lookup table. Every tool is shown —
                      this roll-up exists nowhere else, so it must not truncate
                      to a link that leads to a page without it — each group
                      ranked best-first with its score drawn as a bar, so the
                      standing reads at a glance instead of by comparing digits.

                      One scroll region holds every group with its header sticky.
                      Measured evidence leads, and the documentation group remains
                      reachable by scrolling instead of being clipped out of the
                      card. The region is keyboard focusable so it can be scrolled
                      without a pointer; the header control opens the same list at
                      full height in a side sheet. */}
                  {/* A scrollable region is keyboard focusable by the WAI
                      technique so it can be scrolled without a pointer; the
                      lint rule does not model that exception, so it is disabled
                      here with the named region standing in for interactivity. */}
                  {/* eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex */}
                  <div tabIndex={0}
                    role="region"
                    aria-label="Tools evaluated, all evidence groups"
                    className="flex min-h-0 flex-1 flex-col overflow-y-auto overscroll-contain border-t border-[var(--line)] focus-visible:outline-none focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-[var(--accent)]"
                  >
                    <ToolGroups groups={tools.groups} />
                  </div>
                </SectionCard>
              </div>
            )}
            </div>

            <ToolsSheet
              open={toolsOpen}
              onClose={() => setToolsOpen(false)}
              groups={tools.groups}
              count={tools.all.length}
            />
          </>
        )}
      </div>
    </div>
  );
}
