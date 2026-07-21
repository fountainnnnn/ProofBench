/* Shared UI primitives.
   One button shape, one panel shape, one hairline section header, one skeleton
   block, one inline error, one disclosure. Kept in a single module so the
   vocabulary stays small and every route spells these the same way. */

import { useId, useState } from "react";

const BTN_BASE =
  "inline-flex min-h-10 items-center justify-center gap-1.5 rounded-full px-5 text-[13px] font-medium " +
  "transition-colors duration-150 ease-out-quart focus-visible:outline-none focus-visible:outline-2 " +
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
  `${BTN_BASE} ${DISABLED_FILL} bg-[var(--blue-tint)] text-[var(--ink)] hover:bg-[color-mix(in_oklab,var(--blue)_12%,var(--blue-tint))]`;

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
export const PANEL = "rounded-[24px] bg-[var(--surface)] shadow-[var(--shadow-card)]";

/** Touch targets stay at or above 40px on small screens. */
export const TAP = "min-h-10";

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
        aria-controls={open ? `${id}-body` : undefined}
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
      {open && (
        <div id={`${id}-body`} className="mt-3">
          {children}
        </div>
      )}
    </div>
  );
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
        <span
          aria-hidden="true"
          className="h-1.5 w-1.5 shrink-0 rounded-full border border-current"
        />
      )}
      {basis}
    </span>
  );
}
