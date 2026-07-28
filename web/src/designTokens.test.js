import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

/* The dark theme is declared twice — once for `prefers-color-scheme: dark` and
   once for an operator who pinned dark in Settings — because plain CSS cannot
   share one declaration block between a media query and an attribute selector.
   That duplication has already drifted once: the gloss tokens were added to the
   media block only, so pinned-dark users got light-mode rims (white at 0.92
   alpha) drawn around dark cards.

   These tests are the guard. If you add a token to one dark block, add it to
   the other. */

const css = readFileSync(
  fileURLToPath(new URL("./index.css", import.meta.url)),
  "utf8",
);

/**
 * Locate a selector where it is actually a selector: at the start of a line and
 * followed by its opening brace. A bare indexOf matched the selector NAME
 * written inside the comment above the media block, so this helper silently
 * parsed that block twice and every "the two dark blocks agree" assertion
 * passed vacuously while the blocks genuinely differed.
 */
function selectorIndex(selector) {
  const at = css.search(
    new RegExp(`^\\s*${selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*\\{`, "m"),
  );
  expect(at, `missing selector: ${selector}`).toBeGreaterThan(-1);
  return at;
}

/** Pull the `--token: value;` pairs out of one brace-delimited block. */
function tokensAfter(marker) {
  const start = selectorIndex(marker);
  const open = css.indexOf("{", start);
  const body = css.slice(open + 1, css.indexOf("}", open));
  const pairs = new Map();
  for (const [, name, value] of body.matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) {
    pairs.set(name, value.trim());
  }
  return pairs;
}

const media = tokensAfter(':root:not([data-theme="light"])');
const pinned = tokensAfter(':root[data-theme="dark"]');
const light = tokensAfter(":root");

describe("dark theme is declared identically in both places", () => {
  it("declares the same token names", () => {
    expect([...pinned.keys()].sort()).toEqual([...media.keys()].sort());
  });

  it("declares the same values", () => {
    for (const [name, value] of media) {
      expect(pinned.get(name), `${name} differs between the two dark blocks`).toBe(value);
    }
  });

  it("overrides every colour token the light theme defines", () => {
    /* Tokens that are intentionally theme-independent: geometry, spacing,
       motion, and aliases that resolve through another token. Spacing scales
       with the viewport, never with the theme. */
    const themeless = (name, value) =>
      value.startsWith("var(") ||
      /^--(radius|space|thread-w|canvas$|sidebar$|gap|motion|ease|shadow-btn|bg|text|border)/.test(name);

    const missing = [...light]
      .filter(([name, value]) => !themeless(name, value) && !media.has(name))
      .map(([name]) => name);

    /* --accent-soft and friends resolve via var(), so anything left here is a
       literal colour with no dark counterpart. */
    expect(missing).toEqual([]);
  });
});

describe("the dark atmosphere is declared identically in both places", () => {
  /* Same duplication, same drift: the atmosphere gradients are also written
     twice, and the widening that let the wash reach the sidebar landed on the
     pinned-dark copy only. */
  const rule = (selector) => {
    const start = css.indexOf(selector);
    expect(start, `missing rule: ${selector}`).toBeGreaterThan(-1);
    const open = css.indexOf("{", start);
    return css
      .slice(open + 1, css.indexOf("}", open))
      .replace(/\s+/g, " ")
      .trim();
  };

  it.each([
    ["base wash", ".pb-atmosphere {"],
    ["upper blob", ".pb-atmosphere::before {"],
    ["lower blob", ".pb-atmosphere::after {"],
  ])("matches for the %s", (_label, suffix) => {
    expect(rule(`:root:not([data-theme="light"]) ${suffix}`)).toBe(
      rule(`:root[data-theme="dark"] ${suffix}`),
    );
  });
});

/* Contrast is asserted against the tokens rather than pinned to literal colour
   values, so a future palette swap is free to change every hex but can never
   quietly ship unreadable text. Palettes have already been swapped three times
   here; each swap silently broke at least one pair until it was measured. */
function oklchToLinearRgb(decl) {
  const m = decl.match(/oklch\(\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)/);
  if (!m) return null;
  const [L, C, hDeg] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const h = (hDeg * Math.PI) / 180;
  const a = C * Math.cos(h);
  const b = C * Math.sin(h);
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const mm = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
  return [
    4.0767416621 * l - 3.3077115913 * mm + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * mm - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * mm + 1.707614701 * s,
  ].map((c) => Math.min(1, Math.max(0, c)));
}

function contrast(tokens, fg, bg) {
  const lum = (name) => {
    const rgb = oklchToLinearRgb(tokens.get(name));
    expect(rgb, `${name} is not a literal oklch() value`).toBeTruthy();
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2];
  };
  const [hi, lo] = [lum(fg), lum(bg)].sort((p, q) => q - p);
  return (hi + 0.05) / (lo + 0.05);
}

/* [foreground, background, minimum, what it is]. 4.5 for body copy, 3.0 for
   large text and non-text marks, per WCAG 2.1 AA. */
const PAIRS = [
  ["--ink", "--surface", 4.5, "body on a card"],
  ["--ink", "--paper", 4.5, "body on the canvas"],
  ["--ink", "--surface-2", 4.5, "body on a recessed fill"],
  ["--ink", "--sidebar-bg", 4.5, "body on the rail"],
  ["--ink-2", "--surface", 4.5, "secondary on a card"],
  ["--ink-2", "--paper", 4.5, "secondary on the canvas"],
  ["--ink-2", "--sidebar-bg", 4.5, "secondary on the rail"],
  ["--ink-3", "--surface", 3.0, "muted on a card"],
  ["--ink-3", "--sidebar-bg", 3.0, "muted on the rail"],
  ["--accent", "--surface", 4.5, "accent as text"],
  ["--accent", "--paper", 4.5, "accent as text on the canvas"],
  ["--surface", "--accent", 4.5, "white on an accent fill"],
  ["--surface", "--ink", 4.5, "primary button label"],
  ["--surface", "--btn-primary-hover", 4.5, "primary button label, hovered"],
  ["--ok", "--ok-tint", 4.5, "ok on its own tint"],
  ["--warn", "--warn-tint", 4.5, "warn on its own tint"],
  ["--danger", "--danger-tint", 4.5, "danger on its own tint"],
  ["--hero-text", "--hero-ink", 4.5, "the verdict hero"],
  ["--hero-ink-2", "--hero-ink", 4.5, "verdict hero secondary"],
  ["--profile-ink", "--profile", 4.5, "the profile avatar"],
  ["--code-text", "--code-bg", 4.5, "the console"],
  ["--stone", "--surface", 3.0, "the second chart series"],
];

describe.each([
  ["light", () => light],
  ["dark", () => pinned],
])("%s theme contrast", (_name, get) => {
  const tokens = get();

  it.each(PAIRS)("%s on %s clears %s:1 — %s", (fg, bg, min) => {
    expect(contrast(tokens, fg, bg)).toBeGreaterThanOrEqual(min);
  });

  it("keeps the two chart series distinguishable from each other", () => {
    /* Both are neutral-ish, so they separate by lightness. Below ~1.6:1 two
       adjacent bars stop reading as different series. */
    expect(contrast(tokens, "--accent", "--stone")).toBeGreaterThanOrEqual(1.6);
  });
});
