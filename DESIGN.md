# DESIGN.md — ProofBench design system

Color strategy: **Restrained**. Tinted neutrals toward the accent hue, one accent under 10%
of the surface, used for primary actions, selection, and state indicators only.

## Color (OKLCH; exposed as CSS variables, mapped into Tailwind theme)

| Token | OKLCH | Use |
|---|---|---|
| `--bg` | oklch(0.992 0.002 264) | app background, tinted white |
| `--surface` | oklch(0.985 0.003 264) | cards, panels |
| `--surface-2` | oklch(0.972 0.004 264) | sidebar, table header strip |
| `--border` | oklch(0.905 0.006 264) | hairlines, card borders |
| `--border-strong` | oklch(0.82 0.008 264) | input borders, dividers |
| `--text` | oklch(0.24 0.02 264) | primary text (never pure black) |
| `--text-2` | oklch(0.46 0.015 264) | secondary text, labels |
| `--text-3` | oklch(0.62 0.012 264) | placeholders, captions |
| `--accent` | oklch(0.52 0.19 268) | indigo-violet; primary actions, selection |
| `--accent-hover` | oklch(0.46 0.19 268) | hover on accent |
| `--accent-soft` | oklch(0.95 0.025 268) | selected row, badge tint |
| `--ok` | oklch(0.62 0.15 155) | success / done |
| `--warn` | oklch(0.68 0.15 75) | repair attempts, warnings |
| `--err` | oklch(0.55 0.19 25) | failed, errors |
| `--info` | oklch(0.58 0.12 230) | running, in-progress |
| `--code-bg` | oklch(0.27 0.02 264) | sandbox terminal panels (dark by content, not theme) |

Rules: no `#000`, no `#fff`. Neutrals keep chroma 0.002–0.006. Accent never decorates;
it marks action or selection. State colors appear only on badges, dots, and small text.

## Typography

- Family: Inter (Google Fonts), fallback system-ui stack. One family everywhere.
- Mono: ui-monospace / SFMono stack for logs, code, metric numerals in tables.
- Fixed rem scale (no fluid type): 12, 13, 14 (body), 16, 18, 22, 28. Ratio ~1.125–1.2.
- Weights: 400 body, 500 labels, 600 headings and table headers. No display weights in UI.
- Prose line length 65–75ch; tables and logs may run full width.

## Spacing, shape, elevation

- 4px base grid. Common paddings: 8, 12, 16, 24, 32. Vary rhythm between sections.
- Radius: 6px controls, 10px cards, 999px badges/dots. Same radius vocabulary everywhere.
- Elevation: two soft levels, tinted toward the accent hue, always paired with the 1px
  border. Panels and cards rest on `shadow-card` (0 1px 2px + 0 2px 8px, indigo-tinted at
  4–6% opacity). Hero panels, popovers, and floating surfaces use `shadow-lift`
  (0 2px 6px + 0 10px 24px, 6–10% opacity). No pure-black shadows, no stacked heavy depths.

## Components (all with default, hover, focus, active, disabled, loading states)

- **Button**: one shape (6px radius, 13px font, 500 weight, h-9). Variants: primary (accent
  fill, white text), secondary (surface, border), ghost (no border, text-2). Same shape
  everywhere, including "Run benchmark".
- **Input/textarea**: surface bg, `--border-strong` border, 6px radius, focus = 2px accent
  ring at 40% opacity. Labels 12px, 500 weight, `--text-2`, sentence case.
- **Badge / status dot**: pill, 11px font, tinted bg at 10% of state color + state text.
  Phases: building (info), validating (warn), running (info), done (ok), failed (err).
- **Table**: header strip `--surface-2`, 12px 600 labels, row hover `--accent-soft` at 50%,
  1px row dividers, numerals in mono. Sortable headers get a small caret. Ranked #1 row gets
  accent-soft background, nothing louder.
- **Card**: used sparingly, for self-contained panels (metric summary, report). Never nested.
- **Sidebar**: `--surface-2`, 220px, nav items 13px, selected = `--accent-soft` bg + accent
  text, section labels 11px uppercase tracking-wide `--text-3`.
- **Skeleton**: shimmer-free, solid `--surface-2` blocks with pulse opacity, for table rows
  and report body while loading. No centered spinners.
- **Empty states**: one line of guidance plus one action (button or chip). Never "nothing here".
- **Terminal panels** (sandbox logs): the only dark surface, `--code-bg`, mono 12px, green-grey
  text oklch(0.85 0.02 160). Dark here is content (logs), not theme.

## Motion

- 150–250ms, ease-out-quart. State changes only: hover, panel expand, message append.
- No page-load choreography. No layout-property animation (transform/opacity only).

## Landing page (only brand surface)

- Same tokens. Hero: headline 28–36px, one supporting sentence, one primary CTA ("Open console").
- No gradient text, no hero-metric block, no identical card grid. Feature section uses an
  asymmetric two-column layout: numbered principles list beside a live-looking (static) run
  transcript panel. Sponsor names as plain text badges in one row, `--text-3` 12px.

## Absolute bans (reminder)

Side-stripe accent borders; gradient text; glassmorphism; hero-metric template; identical
card grids; modal-first flows; em dashes in copy; emojis in UI copy.
