/* Shared UI primitives.
   One button shape, one panel shape, one hairline section header, one skeleton
   block, one inline error, one disclosure. Kept in a single module so the
   vocabulary stays small and every route spells these the same way. */

import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";

import StatusIcon from "./StatusIcon.jsx";

/* `active:scale` gives a press something to answer with. 0.97 is deliberately
   small: the button must acknowledge the click without appearing to move on the
   page. Paired with transition-transform so the release eases back rather than
   snapping, and excluded while disabled so a dead control stays dead. */
const BTN_BASE =
  "inline-flex min-h-10 items-center justify-center gap-1.5 rounded-full px-5 text-[13px] font-medium " +
  "transition-[colors,transform] duration-150 ease-out-quart active:scale-[0.97] disabled:active:scale-100 " +
  "focus-visible:outline-none focus-visible:outline-2 " +
  "focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)] disabled:cursor-not-allowed";

// Disabled controls are styled states, not opacity washes: a solid surface-2
// chip in muted ink, never a ghost of the enabled treatment.
const DISABLED_FILL =
  "disabled:bg-[var(--surface-2)] disabled:text-[var(--ink-2)] disabled:shadow-none";
const DISABLED_GHOST =
  "disabled:bg-transparent disabled:text-[var(--ink-2)]";

export const BTN_PRIMARY =
  `${BTN_BASE} ${DISABLED_FILL} bg-[var(--ink)] text-[var(--surface)] hover:bg-[var(--btn-primary-hover)]`;

export const BTN_SECONDARY =
  `${BTN_BASE} ${DISABLED_FILL} bg-[var(--stone-tint)] text-[var(--ink)] hover:bg-[color-mix(in_oklab,var(--stone)_12%,var(--stone-tint))]`;

export const BTN_GHOST =
  `${BTN_BASE} ${DISABLED_GHOST} text-[var(--ink-2)] hover:bg-[var(--surface-2)] hover:text-[var(--ink)]`;

export const BTN_DANGER =
  `${BTN_BASE} ${DISABLED_FILL} bg-[var(--danger-tint)] text-[var(--danger)] font-semibold hover:bg-[color-mix(in_oklab,var(--danger)_14%,var(--danger-tint))]`;

/** Console route title + anchored page-header band, composed consistently. */
export const PAGE_TITLE = "pb-page-title";
export const PAGE_HEADER = "pb-page-header";

export const INPUT =
  "h-10 w-full min-w-0 rounded-[12px] border border-[var(--line-strong)] bg-[var(--surface)] px-3.5 text-[13px] " +
  "text-[var(--ink)] outline-none placeholder:text-[var(--ink-3)] focus-visible:outline-2 " +
  "focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)] disabled:opacity-50";

/** White borderless surface resting on the tinted canvas. Never nested. */
export const PANEL = "pb-glass rounded-[24px] shadow-[var(--shadow-card)]";

/** Touch targets stay at or above 40px on small screens. */
export const TAP = "min-h-10";

/** The class that sweeps one pass of light across a freshly selected segment. */
export const SHEEN_SWIPE = "pb-sheen-swipe";

/**
 * Gate for SHEEN_SWIPE: false until the user actually changes `value`, so the
 * swipe answers a click and does not fire on every page load — four segmented
 * controls all flashing at once on mount would read as decoration rather than
 * as feedback. Stays true afterwards; the animation itself only replays because
 * the class moves from the old segment to the new one.
 *
 * Compares against the mounted value rather than counting effect runs: React
 * StrictMode invokes effects twice in development, so a "skip the first call"
 * ref reports a change that never happened and the sheen fires on load. This
 * form is idempotent — running it twice with the same value does nothing.
 */
export function useSelectionSheen(value) {
  const mounted = useRef(value);
  const [changed, setChanged] = useState(false);

  useEffect(() => {
    if (value !== mounted.current) setChanged(true);
  }, [value]);

  return changed;
}

/**
 * Backend markdown (agent replies, generated reports) carries its own heading
 * levels and would otherwise put a second H1 on the route. These maps demote
 * every markdown heading beneath the heading the surrounding panel already
 * owns, without touching the text.
 */
function demoteHeadings(start) {
  const level = (n) => Math.min(6, start + n);
  const map = {};
  for (let i = 0; i < 6; i += 1) {
    const Tag = `h${level(i)}`;
    map[`h${i + 1}`] = function DemotedHeading({ node, ...props }) {
      return <Tag {...props} />;
    };
  }
  return map;
}

/** For assistant messages, which sit under the route H1 and a panel H2. */
export const MARKDOWN_HEADINGS_IN_THREAD = demoteHeadings(3);

/** For the generated report, which already sits under an H3 named "Report". */
export const MARKDOWN_HEADINGS_IN_REPORT = demoteHeadings(4);

export function Eyebrow({ children, as: Tag = "h2", id }) {
  return (
    <Tag id={id} className="pb-eyebrow">
      {children}
    </Tag>
  );
}

/** Static skeleton geometry: a solid block that holds the shape of the content. */
export function Skeleton({ className = "" }) {
  return <div className={`pb-skeleton ${className}`} aria-hidden="true" />;
}

/**
 * The single shared disclosure vocabulary: a borderless row that folds
 * reference or secondary content away. Chevron rotates, title sits left,
 * an optional summary sits right, and the body mounts only while open so
 * collapsed content is absent from the document rather than merely hidden.
 */
export function Collapse({ title, summary, defaultOpen = false, children, id: providedId }) {
  const [open, setOpen] = useState(defaultOpen);
  const autoId = useId();
  const id = providedId || autoId;
  return (
    <div>
      <button
        type="button"
        aria-expanded={open}
        /* The body is now always in the DOM (it has to be, to animate shut), so
           it always has an id to point at. */
        aria-controls={`${id}-body`}
        onClick={() => setOpen((value) => !value)}
        className="flex min-h-10 w-full items-center justify-between gap-3 rounded-[8px] text-left focus-visible:outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)]"
      >
        <span className="flex min-w-0 items-center gap-2">
          <svg
            aria-hidden="true"
            viewBox="0 0 12 12"
            className={`h-3 w-3 shrink-0 text-[var(--ink-3)] transition-transform duration-150 ${open ? "rotate-90" : ""}`}
            fill="none"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M4.5 2.5 8 6l-3.5 3.5" />
          </svg>
          <span id={`${id}-title`} className="truncate text-[14px] font-semibold text-[var(--ink)]">
            {title}
          </span>
        </span>
        {summary && <span className="shrink-0 text-[12px] text-[var(--ink-2)]">{summary}</span>}
      </button>
      {/* grid-template-rows 1fr -> 0fr animates to the content's own height with
          no JS measurement and no magic max-height that clips tall bodies. The
          inner element owns `overflow: hidden` so the collapsing row actually
          crops rather than spilling.

          `inert` is load-bearing, not decoration: animating shut requires the
          body to stay in the DOM, and a zero-height box with overflow hidden is
          still focusable and still read by screen readers. Without this, every
          collapsed panel would silently add its controls to the tab order. */}
      <div
        id={`${id}-body`}
        inert={open ? undefined : ""}
        className={`grid transition-[grid-template-rows] duration-[260ms] ease-out-quart ${
          open ? "grid-rows-[1fr]" : "grid-rows-[0fr]"
        }`}
      >
        <div className="overflow-hidden">
          <div className="mt-3">{children}</div>
        </div>
      </div>
    </div>
  );
}

/**
 * The moving "you are here" mark. One pill per list, translated to whichever
 * row is selected, so the highlight travels between rows instead of blinking
 * out in one place and in somewhere else — the reader's eye is carried to the
 * new position rather than having to re-find it.
 *
 * The container gets the returned ref; the selected row gets
 * `data-flow-active="true"`. Anything else is up to the caller, so the same
 * primitive serves the nav rail and the session list.
 */
export function useFlowHighlight(activeKey) {
  /* A callback ref held in state, not a useRef: the shell early-returns a
     loading screen while auth resolves, so the list mounts on a LATER render
     than the one this hook first ran on. With a plain ref the measuring effect
     had already run against a null container and never re-ran, and the pill
     silently never appeared. Storing the node in state re-runs the effect the
     moment the container actually exists. */
  const [container, setContainer] = useState(null);
  const [box, setBox] = useState(null);
  /* First placement must not animate: a pill sliding in from the top of the
     list on every page load is an entrance, not feedback. */
  const placed = useRef(false);

  useLayoutEffect(() => {
    if (!container) return undefined;

    const measure = () => {
      const active = container.querySelector('[data-flow-active="true"]');
      if (!active) {
        setBox(null);
        placed.current = false;
        return;
      }
      /* Rect difference plus scrollTop rather than offsetTop: the session list
         is itself a scrolling box, and offsetTop would be read against whatever
         the nearest positioned ancestor happens to be. */
      const c = container.getBoundingClientRect();
      const a = active.getBoundingClientRect();
      setBox({
        top: a.top - c.top + container.scrollTop,
        height: a.height,
        animate: placed.current,
      });
      placed.current = true;
    };

    measure();

    /* Rows resize when the rail is dragged and the list reflows when sessions
       arrive, and either moves the target out from under the pill.

       Guarded rather than assumed: ResizeObserver does not exist in jsdom, and
       an unguarded constructor threw during render and took the whole Shell
       down in the test environment. The pill still measures on every activeKey
       change without it; the observer only adds re-measurement on resize. */
    if (typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(measure);
    observer.observe(container);
    for (const child of container.children) observer.observe(child);
    return () => observer.disconnect();
  }, [container, activeKey]);

  return { containerRef: setContainer, box };
}

/** The pill itself. Renders nothing when no row is selected. */
export function FlowHighlight({ box, className = "" }) {
  if (!box) return null;
  return (
    <span
      aria-hidden="true"
      className={`pb-flow-highlight ${className}`}
      style={{
        transform: `translateY(${box.top}px)`,
        height: `${box.height}px`,
        transition: box.animate ? undefined : "none",
      }}
    />
  );
}

/**
 * Fit a list to the height it is given instead of scrolling inside it. Renders
 * every row (so the overflow can be measured), then caps the visible height at
 * the last row that fits whole and reports how many were left over, for the
 * caller to summarise as a "see all" footer.
 *
 * This is the alternative to an inner scrollbar on a dashboard card: the card
 * keeps its place in a non-scrolling page and shows as much as the viewport
 * allows, rather than becoming a tiny scroll port. What overflows is not hidden
 * silently — the returned `hidden` count must be surfaced, or the card would
 * quietly under-report.
 *
 * The list element gets `listRef` and `overflow: hidden` with the returned
 * `maxHeight`; each row carries `data-fit-item`; the footer, if any, gets
 * `footerRef` so its height is reserved. All three share one outer box via
 * `outerRef`, whose measured height is the budget.
 */
export function useFitList(depsKey) {
  const [outer, setOuter] = useState(null);
  const [list, setList] = useState(null);
  const footerRef = useRef(null);
  const [fit, setFit] = useState({ maxHeight: undefined, hidden: 0 });

  useLayoutEffect(() => {
    if (!outer || !list) return undefined;

    const measure = () => {
      const items = list.querySelectorAll("[data-fit-item]");
      if (!items.length) {
        setFit((prev) => (prev.maxHeight === undefined && prev.hidden === 0 ? prev : { maxHeight: undefined, hidden: 0 }));
        return;
      }
      const listTop = list.getBoundingClientRect().top;
      const bottoms = Array.from(items, (el) => el.getBoundingClientRect().bottom - listTop);
      const total = bottoms.length;
      const countWithin = (budget) => {
        let n = 0;
        for (const b of bottoms) {
          if (b <= budget + 0.5) n += 1;
          else break;
        }
        return n;
      };

      const full = outer.clientHeight;
      const apply = (maxHeight, hidden) =>
        setFit((prev) => (prev.maxHeight === maxHeight && prev.hidden === hidden ? prev : { maxHeight, hidden }));

      if (countWithin(full) >= total) {
        apply(undefined, 0);
        return;
      }
      // Something overflows, so a footer will show; reserve its height before
      // deciding how many rows fit above it. The cut is always a row boundary
      // (bottoms[n-1]).
      const footerH = footerRef.current ? footerRef.current.offsetHeight : 40;
      let n = countWithin(full - footerH);
      // Prefer one row over an all-footer card, but only when a whole row fits
      // in the budget without the footer reserve. If even one row is taller than
      // the card, show none rather than clip a row mid-content — the footer then
      // stands in for the whole list.
      if (n === 0 && bottoms[0] <= full + 0.5) n = 1;
      apply(n > 0 ? bottoms[n - 1] : 0, total - n);
    };

    measure();
    if (typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(measure);
    observer.observe(outer);
    observer.observe(list);
    for (const child of list.children) observer.observe(child);
    return () => observer.disconnect();
  }, [outer, list, depsKey]);

  return { outerRef: setOuter, listRef: setList, footerRef, maxHeight: fit.maxHeight, hidden: fit.hidden };
}

/** Contextual inline error, rendered where the failing thing lives. */
export function InlineError({ children, onRetry, retryLabel = "Retry" }) {
  if (!children) return null;
  return (
    <div
      role="alert"
      className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-[12px] bg-[var(--danger-tint)] px-3.5 py-2.5 text-[13px] text-[var(--danger)]"
    >
      <span className="pb-contain min-w-0 flex-1">{children}</span>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="shrink-0 text-[13px] font-medium text-[var(--danger)] underline underline-offset-2"
        >
          {retryLabel}
        </button>
      )}
    </div>
  );
}

/**
 * Evidence basis label. The only place the app is allowed to say how a number
 * was produced, and it only ever repeats what the backend declared.
 * Verified execution and documentation-only comparison never share a treatment.
 * `dark` renders the pill on the ink verdict hero instead of a light surface.
 */
export function BasisTag({ basis, dark = false }) {
  if (!basis) return null;
  const verified = /verified in daytona/i.test(basis);
  const partly = /partly verified/i.test(basis);
  const tone = dark
    ? partly
      ? "bg-[color-mix(in_oklab,var(--surface)_10%,transparent)] text-[oklch(0.80_0.11_85)]"
      : verified
        ? "bg-[color-mix(in_oklab,var(--surface)_10%,transparent)] text-[oklch(0.80_0.13_155)]"
        : "border border-dashed border-[color-mix(in_oklab,var(--hero-ink-2)_55%,transparent)] text-[var(--hero-ink-2)]"
    : partly
      ? "bg-[var(--warn-tint)] text-[var(--warn)]"
      : verified
        ? "bg-[var(--ok-tint)] text-[var(--ok)]"
        : "border border-dashed border-[var(--line-strong)] text-[var(--ink-2)]";
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[12px] font-medium ${tone}`}
    >
      {verified || partly ? (
        <svg
          aria-hidden="true"
          viewBox="0 0 12 12"
          className="h-3 w-3 shrink-0"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M2.5 6.5 5 9l4.5-6" />
        </svg>
      ) : (
        <StatusIcon tone="docs" size={12} />
      )}
      {basis}
    </span>
  );
}
