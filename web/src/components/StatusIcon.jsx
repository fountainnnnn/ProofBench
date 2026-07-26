/* One status glyph family for the whole console.
   A coloured dot is a status *colour* with no meaning of its own: it forces the
   reader to learn a colour key, and it says nothing at all in greyscale or to
   anyone who cannot separate the hues. Each state here carries a shape that
   means the state, so colour becomes reinforcement rather than the only signal.

   Tones map to the semantic tokens: ok, warn, danger, running, pending, docs.
   Callers pass the tone they already compute; the shape is decided here so the
   same state can never be drawn two different ways on two pages. */

const BOX = {
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.8,
  strokeLinecap: "round",
  strokeLinejoin: "round",
};

function Ok(props) {
  return (
    <svg viewBox="0 0 24 24" {...BOX} {...props}>
      <circle cx="12" cy="12" r="9" />
      <path d="m8.5 12.2 2.4 2.4 4.6-5.2" />
    </svg>
  );
}

function Warn(props) {
  return (
    <svg viewBox="0 0 24 24" {...BOX} {...props}>
      <path d="M10.6 3.9 2.5 18a1.6 1.6 0 0 0 1.4 2.4h16.2a1.6 1.6 0 0 0 1.4-2.4L13.4 3.9a1.6 1.6 0 0 0-2.8 0Z" />
      <path d="M12 9.5v4" />
      <path d="M12 17.1h.01" />
    </svg>
  );
}

function Danger(props) {
  return (
    <svg viewBox="0 0 24 24" {...BOX} {...props}>
      <circle cx="12" cy="12" r="9" />
      <path d="M15 9l-6 6M9 9l6 6" />
    </svg>
  );
}

/* Concentric arcs: a broadcast mark, which reads as "in progress" without
   borrowing the spinner that means "the page is loading". */
function Running(props) {
  return (
    <svg viewBox="0 0 24 24" {...BOX} {...props}>
      <circle cx="12" cy="12" r="2.4" />
      <path d="M7.8 7.8a6 6 0 0 0 0 8.4" />
      <path d="M16.2 16.2a6 6 0 0 0 0-8.4" />
    </svg>
  );
}

function Pending(props) {
  return (
    <svg viewBox="0 0 24 24" {...BOX} {...props}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7.4v4.9l3.1 1.8" />
    </svg>
  );
}

/* Documentation-only evidence: a page, never a tick. The distinction between
   "executed" and "read from the docs" is the product's core claim, so the two
   must not share a shape. */
function Docs(props) {
  return (
    <svg viewBox="0 0 24 24" {...BOX} {...props}>
      <path d="M6 3.5h7.5L18 8v12.5H6z" />
      <path d="M13.2 3.7V8H18" />
      <path d="M9 12.5h6M9 16h4" />
    </svg>
  );
}

const SHAPES = {
  ok: Ok,
  warn: Warn,
  danger: Danger,
  err: Danger,
  running: Running,
  info: Running,
  pending: Pending,
  neutral: Pending,
  docs: Docs,
};

export default function StatusIcon({ tone = "pending", size = 13, className = "", pulse = false }) {
  const Shape = SHAPES[tone] || Pending;
  return (
    <Shape
      width={size}
      height={size}
      aria-hidden="true"
      className={`shrink-0 ${pulse ? "animate-pulse" : ""} ${className}`}
    />
  );
}
