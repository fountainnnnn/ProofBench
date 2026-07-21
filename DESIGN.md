# DESIGN.md — ProofBench design system

Color strategy: **Restrained, single-hue petrol**. The entire neutral family
(canvas, surfaces, ink, accent, verdict hero) is one petrol hue (OKLCH 210-220)
at different lightness, so the brand colour literally is the verdict rather
than a decoration. Ink is reserved for the few elements that should lead the
eye: primary actions, the selected nav item, and the single verdict hero card
(deep petrol, not near-black). The petrol accent marks interaction (links,
focus, selection, live state) and nothing else. Separation comes from fill and
whitespace, not hairlines.

Two themes ship from the same tokens: light is the default (conference-room
legibility), dark inverts the same petrol family. The system preference is
followed unless the operator pins a theme in Settings (html[data-theme],
persisted as localStorage "pb-theme"); index.html applies the pin pre-paint.
Every token keeps its role across themes (--ink is "the strong colour",
--surface is "the card colour"), so component markup is theme-blind. The hero
card uses --hero-text for its foreground, never --surface.

## Color (OKLCH; exposed as CSS variables, mapped into Tailwind theme)

| Token | OKLCH | Use |
|---|---|---|
| `--bg` / `--paper` | oklch(0.955 0.008 210) | app canvas, soft cool tint |
| `--surface` | oklch(0.995 0.002 210) | cards, sidebar, chrome |
| `--surface-2` | oklch(0.938 0.009 210) | chips, inputs, hover fills |
| `--line` | oklch(0.90 0.009 210) | rare hairlines (compact list dividers) |
| `--line-strong` | oklch(0.83 0.011 210) | scrollbars, strong dividers |
| `--text` / `--ink` | oklch(0.23 0.022 220) | primary text, primary buttons, nav pill |
| `--text-2` | oklch(0.44 0.022 220) | secondary text, labels |
| `--text-3` | oklch(0.51 0.02 220) | placeholders, captions |
| `--accent` | oklch(0.50 0.105 215) | links, focus, selection, running state |
| `--accent-tint` | oklch(0.935 0.028 210) | selection fill, drag-over |
| `--btn-primary-hover` | oklch(0.33 0.035 220) | hover on ink primary buttons |
| `--hero-ink` | oklch(0.30 0.055 220) | the verdict hero card (only dark card) |
| `--hero-ink-2` | oklch(0.80 0.022 210) | secondary text on the hero card |
| `--hero-text` | oklch(0.985 0.004 210) | hero card foreground (never `--surface`) |
| `--ok` / `--warn` / `--danger` | hues 150 / 75 / 25, L≈0.47 | status dots, pill text |
| `--code-bg` | oklch(0.25 0.03 220) | sandbox terminal panels (dark by content) |

Rules: no `#000`, no `#fff`. Semantic colors appear only on dots, pills, and
small status text; each pairs with its `-tint` at 4.5:1+. The accent never
decorates. Status chrome is silent when healthy: no "connected" indicators, no
deployment-mode pills; a warning appears only when the server is unreachable.

## Typography

- Family: Inter Variable (self-hosted via `@fontsource-variable/inter`) for
  all UI. Mono: JetBrains Mono Variable (self-hosted) for metrics,
  identifiers, logs; `.pb-mono` opts in. Display: Source Serif 4 Variable
  (`.pb-display`), used only where the product speaks a verdict: the landing
  hero, verdict/winner names, and route titles. Never in buttons, labels,
  body, or data.
- Fixed rem scale: 12, 13, 14 (body), 16, 20, 24, 28; heroes 36–56.
- Weights: 400 body, 500 labels, 600 headings. Tracking: -0.01em headings,
  -0.02 to -0.03em titles and heroes.
- Page headers: 24px semibold title + one-line 14px `--text-2` subtitle.
- Prose line length 65–75ch. `tabular-nums` everywhere.

## Spacing, shape, elevation

- 4px base grid; generous rhythm. Page padding py-8/10, cards p-5/6, list rows
  py-3+. Breathing room is the layout tool, not dividers.
- Radius: 12px controls, 16–20px cards, 24px dialogs, 999px pills/badges/dots.
- No visible borders on cards, buttons, inputs, or callouts. Hairlines are
  allowed only as `divide-y` separators inside compact single-line lists.
- Elevation: cards carry a whisper shadow (`--shadow-card`), floating overlays
  a soft float (`--shadow-lift`). Inputs are gray fills, not bordered boxes.

## Components (all with default, hover, focus, active, disabled, loading states)

- **Button**: 12px radius, 13px font, 500 weight, min-h-10. Primary = ink fill
  + near-white text. Secondary = `--surface-2` fill. Ghost = `--text-2`.
  Danger = danger-tint fill + danger text.
- **Input/textarea**: `--surface-2` fill, 12px radius, no border, focus = 2px
  accent outline. Labels 12px 500 `--text-2`.
- **Pill / badge**: `rounded-full`, 12px font. Provenance is structural, not
  just tinted: verified = solid `--ok-tint` fill with a check glyph; partly
  verified = solid `--warn-tint` fill with a check glyph; documentation-only =
  dashed outline chip (border-dashed `--line-strong`) with an open dot, so the
  distinction survives grayscale. Verified and documentation-only evidence
  never share a treatment.
- **Card / panel**: `PANEL` = white, 20px radius, whisper shadow. Never nested.
- **Verdict hero**: the one ink-dark card (`--hero-ink`, 20px radius, p-6).
  Winner name 28–32px semibold, score in mono, basis pill in the dark variant
  (`BasisTag dark`), secondary text `--hero-ink-2`. Exactly one per screen.
- **Sidebar**: white, flush, 248px, icon + label items 13px with 12px radius.
  Selected = the black pill (ink bg, surface text). No section labels needed.
- **Nav icons**: one stroke family, 24px box, 1.5 stroke, round caps,
  `currentColor`.
- **Table**: white card, gray 12px semibold labels, no header strip, no row
  fills; rows separate by whitespace. Rank 1 gets the black number chip. Mono
  numerals. Sort headers use the accent. `.pb-stack-table` restacks < 720px.
- **Skeleton**: solid `--surface-2` blocks, opacity pulse. No spinners.
- **Empty states**: teach the interface. The benchmark empty state is the
  chat-hero: centered 28–36px question, one supporting line, quick-start cards
  that fill the composer (never auto-send).
- **Terminal panels**: dark `--code-bg`, mono 12px, `--code-text`, 12px radius.
- **Focus**: one visible 2px accent outline, 2px offset, everywhere.

## Motion

- 150–250ms, ease-out-quart. Color/shadow transitions only; transform/opacity
  if needed. No page-load choreography, no layout-property animation.

## Landing page (only brand surface)

- Same tokens. Hero headline 40–56px, -0.03em tracking, one supporting
  paragraph, primary CTA ("Run a benchmark", ink) + secondary ("See a sample
  verdict"). Sample verdict in one white borderless card.
- No gradient text, no hero-metric block, no identical card grid, no footer
  chrome. Third-party integration names do not appear anywhere in the product.

## Absolute bans (reminder)

Side-stripe accent borders; gradient text; glassmorphism; hero-metric template;
identical card grids; modal-first flows; em dashes in copy; emojis in UI copy;
borders as the default separator (use fill and whitespace); status chrome for
healthy states.
