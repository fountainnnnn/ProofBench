import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { getResults, listSessions } from "../api.js";
import { safeVisibleText, sanitizeForDisplay } from "../displaySafety.js";
import { authoritativeProvenance, canRenderMetrics } from "../provenance.js";
import { relativeTime } from "../relativeTime.js";
import { buildCanonicalRows, buildDecision, primaryMetricKey } from "../resultsModel.js";
import { BTN_PRIMARY, InlineError, PAGE_HEADER, PAGE_TITLE, PANEL, Skeleton, useFitList } from "../components/ui.jsx";
import HeaderActions from "../components/HeaderActions.jsx";
import StatusIcon from "../components/StatusIcon.jsx";
import { ContributionCalendar, RankBars } from "../components/charts.jsx";

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

function SectionCard({ title, count, children, grow = false }) {
  return (
    <section
      className={`${PANEL} flex flex-col overflow-hidden ${grow ? "min-h-0 flex-1" : ""}`}
      aria-label={title}
    >
      <div className="flex items-baseline justify-between gap-3 px-5 pb-1 pt-4">
        <h2 className="text-[16px] font-semibold tracking-[-0.01em] text-[var(--ink)]">{title}</h2>
        {count !== null && count !== undefined && (
          <span className="pb-mono text-[12px] text-[var(--ink-3)]">{count}</span>
        )}
      </div>
      {children}
    </section>
  );
}

/* One finished benchmark, read as its conclusion rather than its status. */
function VerdictRow({ item }) {
  const score = formatMetric(item.metricKey, item.winner[item.metricKey]);
  return (
    <li data-fit-item className="flex flex-col">
      <Link
        to={`/app/benchmark?session=${encodeURIComponent(item.sessionId)}`}
        onClick={() => localStorage.setItem("proofbench.activeSessionId", item.sessionId)}
        className="flex min-h-16 items-center gap-4 px-5 py-3 transition-colors duration-150 hover:bg-[var(--surface-2)] focus-visible:bg-[var(--surface-2)] focus-visible:outline-none"
      >
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-baseline gap-x-2.5 gap-y-0.5">
            <span className="pb-contain truncate text-[15px] font-medium text-[var(--ink)]">
              {safeVisibleText(item.winner.name)}
            </span>
            {score && (
              <span className="pb-mono shrink-0 text-[12px] text-[var(--ink-2)]">
                {METRIC_LABEL[item.metricKey] || "Score"} {score}
              </span>
            )}
          </span>
          <span className="pb-contain mt-0.5 block truncate text-[12px] text-[var(--ink-2)]">
            won {item.rankedCount === 1 ? "the only entry" : `over ${item.rankedCount - 1} other${item.rankedCount === 2 ? "" : "s"}`}
            {" in "}
            {safeVisibleText(item.title)}
          </span>
        </span>
        <time
          className="pb-mono shrink-0 text-[12px] text-[var(--ink-3)]"
          dateTime={item.createdAt}
          title={new Date(item.createdAt).toLocaleString()}
        >
          {relativeTime(item.createdAt)}
        </time>
        <span className="shrink-0 text-[var(--ink-3)]">
          <ArrowIcon />
        </span>
      </Link>
      {/* The spread behind the verdict. A headline of "X won" is a claim; the
          bars are the margin it won by, which is the part a reader needs to
          judge whether the result is decisive or a coin flip. */}
      <div className="px-5 pb-4">
        <RankBars
          rows={item.rows}
          metricKey={item.metricKey}
          format={(v) => formatMetric(item.metricKey, v) ?? "n/a"}
          max={item.metricKey === "rating" ? 100 : undefined}
          hue={METRIC_HUE[item.metricKey] || "var(--accent)"}
        />
      </div>
    </li>
  );
}

/* Every candidate these benchmarks have judged, and how it did at its best.
   Assembled across runs, so it exists NOWHERE else in the product — which is
   exactly why this list must show every tool rather than truncate to a "see
   all" link: there is no other page for that link to lead to. It reads in two
   columns so the whole set fits the card at a dashboard's density. */
function ToolCell({ tool }) {
  const score = formatMetric(tool.metricKey, tool.best);
  return (
    <div className="flex min-w-0 items-center gap-2 px-5 py-2">
      <span className="pb-contain min-w-0 flex-1 truncate text-[13px] text-[var(--ink)]">
        {safeVisibleText(tool.name)}
      </span>
      {tool.wins > 0 && (
        <span className="shrink-0 text-[var(--ok)]" title="Won a benchmark" aria-label="Won a benchmark">
          <StatusIcon tone="ok" size={13} />
        </span>
      )}
      {score && (
        <span className="pb-mono shrink-0 text-[12px] text-[var(--ink-2)]">
          {score}
        </span>
      )}
    </div>
  );
}

/* Tools split by how their number was arrived at, because the two groups are
   not comparable. The header names the scale once instead of every row
   repeating it, and carries the group's hue as a small key. */
function ToolGroupHeader({ metricKey }) {
  return (
    <div className="flex items-center gap-2 bg-[var(--surface-2)] px-5 py-1.5">
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

/** Footer that stands in for verdicts that did not fit. Verdicts alone earns a
    link: a verdict IS a completed run, so Runs is where the rest actually live
    and can be opened. (Tools have no such page, so that list never truncates.) */
function MoreLink({ hidden, noun, footerRef }) {
  if (hidden <= 0) return null;
  return (
    <Link
      ref={footerRef}
      to="/app/runs"
      className="mt-auto flex shrink-0 items-center justify-center gap-1.5 border-t border-[var(--line)] px-5 py-2.5 text-[12px] font-medium text-[var(--ink-2)] transition-colors duration-150 hover:bg-[var(--surface-2)] hover:text-[var(--ink)]"
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
            const decision = buildDecision(buildCanonicalRows(metrics, isAssessment), isAssessment);
            if (!decision?.winner) return null;
            return {
              sessionId: s.id,
              title: s.title || "Untitled benchmark",
              createdAt: s.created_at,
              metricKey: primaryMetricKey(isAssessment),
              winner: decision.winner,
              rankedCount: decision.rankedCount,
              rows: buildCanonicalRows(metrics, isAssessment),
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
        const entry = byName.get(name) || { name, appearances: 0, wins: 0, best: null, metricKey: v.metricKey };
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
    return { all, groups: [...groups.entries()] };
  }, [verdicts]);

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

  const resume = sessions.find((s) => s.is_running) || sessions[sessions.length - 1] || null;
  const nothingYet = !loading && sessions.length === 0;

  // Verdicts fit to the height they are handed; the overflow is a link to Runs,
  // where those completed runs actually live. Tools never truncate — they read
  // in two columns instead, since Overview is their only home.
  const verdictFit = useFitList(verdicts.length);

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
            <h1 className={PAGE_TITLE}>Overview</h1>
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

      <div className="mx-auto flex w-full min-h-0 max-w-canvas flex-1 flex-col gap-4 overflow-y-auto px-4 pb-6 pt-5 sm:px-8 lg:overflow-hidden">
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
                className={`${PANEL} flex min-h-14 shrink-0 items-center gap-3 px-5 py-3 transition-colors duration-150 hover:bg-[var(--surface-2)]`}
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
                <div className="px-5 pb-4 pt-1">
                  <p className="mb-2.5 text-[12px] text-[var(--ink-2)]">Benchmarks started</p>
                  <ContributionCalendar days={activity} />
                </div>
              </SectionCard>
            </div>

            <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 lg:grid-cols-12">
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
                    className="divide-y divide-[var(--line)] overflow-hidden"
                  >
                    {verdicts.map((item) => (
                      <VerdictRow key={item.sessionId} item={item} />
                    ))}
                  </ul>
                  <MoreLink hidden={verdictFit.hidden} noun="verdict" footerRef={verdictFit.footerRef} />
                </div>
              )}
            </SectionCard>

            </div>

            {tools.all.length > 0 && (
              <div className="flex min-h-0 flex-col lg:col-span-5">
                <SectionCard title="Tools evaluated" count={tools.all.length} grow>
                  {/* Two columns, every tool shown. This list is the roll-up
                      that exists nowhere else, so it must not truncate to a link
                      that leads to a page without it — instead it halves its own
                      height by reading in two columns. */}
                  <div className="min-h-0 flex-1 overflow-hidden border-t border-[var(--line)]">
                    {tools.groups.map(([metricKey, group]) => (
                      <div key={metricKey}>
                        <ToolGroupHeader metricKey={metricKey} />
                        <ul className="grid grid-cols-1 sm:grid-cols-2">
                          {group.map((tool, index) => (
                            <li
                              key={tool.name}
                              className={`min-w-0 border-t border-[var(--line)] ${
                                index % 2 === 1 ? "sm:border-l" : ""
                              }`}
                            >
                              <ToolCell tool={tool} />
                            </li>
                          ))}
                        </ul>
                      </div>
                    ))}
                  </div>
                </SectionCard>
              </div>
            )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
