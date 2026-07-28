import { useLayoutEffect, useState } from "react";

/* Chart primitives, hand-built: no chart library, no canvas, just tokens.

   Both forms here plot MAGNITUDE, so both use a single sequential hue rather
   than a categorical palette. There is no identity to encode (a candidate is
   not a "series" that recurs across charts), and a categorical scheme would
   spend colour on distinctions the reader does not need to make. Emphasis
   carries the story instead: the winner in accent, the rest in a recessive
   gray. Text always wears ink tokens, never the mark's colour.

   Mark specs held to: bars capped thin, 4px rounded data-end and square at the
   baseline, 2px surface gap between neighbours, hairline recessive axis. */

/* Calendar heatmap, the shape GitHub made legible: one column per week, one row
   per weekday, colour by count. It answers a different question from the column
   chart it replaces — not "how many on each of the last fourteen days" but "what
   does the rhythm of this deployment look like", which is what a long window is
   for. Weekday rows make cadence visible (weekends empty, a Monday habit) in a
   way a flat row of columns cannot.

   Sequential ramp, single hue: count is magnitude, so it gets one hue at four
   steps mixed toward the recessed surface. An empty day is the bare surface
   rather than a pale tint of the ramp, so "nothing happened" never reads as a
   low value. */
const CELL = 11;
const CELL_GAP = 3;
const WEEK_WIDTH = CELL + CELL_GAP;
/* GitHub labels alternate rows to keep the axis from crowding the cells. */
const WEEKDAY_LABELS = [null, "Mon", null, "Wed", null, "Fri", null];

function levelColor(level) {
  if (level === 0) return "var(--surface-2)";
  const mix = [28, 52, 76, 100][level - 1];
  return `color-mix(in oklab, var(--accent) ${mix}%, var(--surface-2))`;
}

export function ContributionCalendar({ days, maxWeeks = 53 }) {
  const [container, setContainer] = useState(null);
  const [weeks, setWeeks] = useState(maxWeeks);

  /* How much history is shown is a function of how much room there is. A fixed
     53 weeks either overflows a narrow card or wastes a wide one, and this card
     now has to live inside a viewport that never scrolls. */
  useLayoutEffect(() => {
    if (!container) return undefined;
    const measure = () => {
      const room = container.clientWidth;
      if (!room) return;
      setWeeks(Math.max(8, Math.min(maxWeeks, Math.floor(room / WEEK_WIDTH))));
    };
    measure();
    if (typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(measure);
    observer.observe(container);
    return () => observer.disconnect();
  }, [container, maxWeeks]);

  const shown = days.slice(Math.max(0, days.length - weeks * 7));
  const max = Math.max(1, ...shown.map((d) => d.count));
  const total = shown.reduce((sum, d) => sum + d.count, 0);

  /* A month label sits above the first week that contains that month's first
     visible day, which is how the year reads as a timeline rather than as an
     undifferentiated field of squares. */
  const months = [];
  for (let w = 0; w < shown.length / 7; w += 1) {
    const first = shown[w * 7];
    if (!first) continue;
    const month = first.date.getMonth();
    if (months.length === 0 || months[months.length - 1].month !== month) {
      /* Skip a label with no room before the next one crowds it. */
      if (months.length === 0 || w - months[months.length - 1].week >= 3) {
        months.push({ week: w, month, label: first.date.toLocaleDateString(undefined, { month: "short" }) });
      }
    }
  }

  return (
    <div>
      <div className="flex gap-2">
        <div
          aria-hidden="true"
          className="grid shrink-0 pt-[18px] text-[10px] leading-none text-[var(--ink-3)]"
          style={{ gap: `${CELL_GAP}px`, gridTemplateRows: `repeat(7, ${CELL}px)` }}
        >
          {WEEKDAY_LABELS.map((label, i) => (
            <span key={i} className="flex items-center">{label}</span>
          ))}
        </div>

        <div ref={setContainer} className="min-w-0 flex-1 overflow-hidden">
          <div className="relative h-[14px] text-[10px] leading-none text-[var(--ink-3)]">
            {months.map((m) => (
              <span key={`${m.week}-${m.month}`} className="absolute top-0" style={{ left: m.week * WEEK_WIDTH }}>
                {m.label}
              </span>
            ))}
          </div>
          <div
            className="grid grid-flow-col"
            style={{ gap: `${CELL_GAP}px`, gridTemplateRows: `repeat(7, ${CELL}px)` }}
            role="img"
            aria-label={
              total === 0
                ? `No benchmarks started in the last ${weeks} weeks`
                : `${total} benchmarks started over the last ${weeks} weeks, by day`
            }
          >
            {shown.map((day) => {
              const level = day.count === 0 ? 0 : Math.min(4, Math.ceil((day.count / max) * 4));
              return (
                <span
                  key={day.key}
                  className="rounded-[2px]"
                  style={{ width: CELL, height: CELL, backgroundColor: levelColor(level) }}
                  title={`${day.label}: ${day.count} ${day.count === 1 ? "benchmark" : "benchmarks"}`}
                />
              );
            })}
          </div>
        </div>
      </div>

      <div className="mt-2 flex items-center gap-1.5 text-[10px] text-[var(--ink-3)]">
        <span className="mr-auto">
          {total === 0 ? "No benchmarks in this window" : `${total} in the last ${weeks} weeks`}
        </span>
        <span>Less</span>
        {[0, 1, 2, 3, 4].map((level) => (
          <span
            key={level}
            aria-hidden="true"
            className="rounded-[2px]"
            style={{ width: CELL - 2, height: CELL - 2, backgroundColor: levelColor(level) }}
          />
        ))}
        <span>More</span>
      </div>
    </div>
  );
}

/* The candidate spread for one finished benchmark: every entry it ranked, on
   that benchmark's own scale. Kept to one benchmark per chart on purpose — a
   documentation rating and a measured accuracy are different scales, and
   plotting them on a shared axis would be a two-scale chart in disguise.

   Hue encodes EVIDENCE TYPE, not value. Candidate names are nominal, so
   colouring bars by their score would spend the identity channel re-encoding
   what bar length already says. What the hue does carry is the product's real
   distinction: sage where a number was measured by execution, blue where it
   was rated from documentation. Within a chart the winner takes the full hue
   and the rest a muted step of the SAME hue, so the chart reads as one family
   and the emphasis still lands. */
export function RankBars({ rows, metricKey, format, max, hue = "var(--accent)" }) {
  const ceiling = max ?? Math.max(...rows.map((r) => Number(r[metricKey]) || 0), 0);
  return (
    <ul className="flex flex-col gap-1.5">
      {rows.map((row) => {
        const value = Number(row[metricKey]);
        const finite = Number.isFinite(value);
        const pct = finite && ceiling > 0 ? Math.max(0, (value / ceiling) * 100) : 0;
        const winner = row.canonicalRank === 1;
        return (
          <li key={row.name} className="flex items-center gap-3">
            <span
              className={`w-[13ch] shrink-0 truncate text-[12px] ${
                winner ? "font-medium text-[var(--ink)]" : "text-[var(--ink-2)]"
              }`}
              title={row.label || row.name}
            >
              {row.label || row.name}
            </span>
            <span className="relative h-2 min-w-0 flex-1 overflow-hidden rounded-full bg-[var(--surface-2)]">
              <span
                className="absolute inset-y-0 left-0 rounded-full"
                style={{
                  width: `${pct}%`,
                  backgroundColor: winner
                    ? hue
                    : `color-mix(in oklab, ${hue} 38%, var(--surface-2))`,
                }}
              />
            </span>
            <span className="pb-mono w-[7ch] shrink-0 text-right text-[11px] text-[var(--ink-2)]">
              {finite ? format(value) : "n/a"}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
