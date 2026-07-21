# PRODUCT.md — ProofBench

## Current stage

ProofBench is a proprietary, source-visible pre-release, written and run locally by one
developer. There is no hosted instance and no third-party user today, so the only actual
user is the copyright holder. See
[docs/adr/0001-local-product-boundary.md](docs/adr/0001-local-product-boundary.md).

## Future target users

If ProofBench is ever offered to others, the intended users are engineering, security,
procurement, and platform teams comparing tools before adoption. They would need an
auditable answer on their own data, clear separation between measured and historical synthetic
evidence, and a report they can defend to another stakeholder. Operators would also need
explicit tenant, retention, quota, and credential boundaries. Those boundaries are built
and tested now, which is why the product is designed against this audience even though it
does not serve them yet.

## Product Purpose

ProofBench benchmarks tools (OCR libraries, hosted vision APIs, anything integrable from
public documentation) against the user's own labelled data. An orchestrator agent discovers
candidates, reads their docs, builds integrations, runs them in isolated Daytona sandboxes,
and scores results deterministically against ground truth. The UI must make three things
obvious within ten seconds: what the agent is doing, why the numbers are trustworthy, and
which tool won.

## Register

**product** (app UI: chat console, runs dashboard, datasets, settings). The landing page is
the only brand surface; it borrows the same restrained system.

## Theme (physical scene)

A technical buyer reviews results on a laptop during a decision meeting and later shares the
report on a bright conference-room display. Every status badge, table row, and log line must
remain legible in both settings. Use a light, high-contrast theme with no meaning carried by
subtle color alone.

## Tone and Copy

- Plain, confident, technical. Short sentences. No exclamation marks.
- No em dashes. Commas, colons, periods, parentheses instead.
- Status language is literal: "building", "validating", "running", "done", "failed".
- Numbers speak first; narrative second.

## Anti-references

- Not a dark "hacker terminal" aesthetic. Not SaaS-cream with pastel blobs.
- Not a chatbot toy. The chat is a console for launching benchmarks, not the product itself.
- No glassmorphism, no gradient text, no identical icon-card grids, no hero-metric template.

## Strategic Principles

1. Trust through evidence: every screen leads to the numbers and how they were produced.
2. The agent's work is visible: tool calls and sandbox logs are first-class content.
3. Familiar patterns over invention: standard nav, standard tables, standard forms.
4. Legibility beats designer cleverness, always.
5. Real-only: there is no demo or simulated execution mode. Sample datasets may
   ship synthetic input images, but every metric shown is genuinely measured.
