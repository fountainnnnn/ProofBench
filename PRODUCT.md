# PRODUCT.md — ProofBench

## Current stage

ProofBench is a proprietary, source-visible pre-release operated by one
developer. It supports the local single-operator shape and a narrow Railway
deployment for one invited client's end-to-end trial. The trial is not general
availability and makes no availability, support, or retention promise beyond
what the operator separately discloses to that client. See
[ADR-0002](docs/adr/0002-railway-client-trial.md).

## Target users

The intended users are engineering, security,
procurement, and platform teams comparing tools before adoption. They would need an
auditable answer on their own data, clear separation between measured and historical synthetic
evidence, and a report they can defend to another stakeholder. Operators would also need
explicit tenant, retention, quota, and credential boundaries. The current
client trial validates this workflow with one invited tenant; it is not yet a
multi-customer launch.

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
- Status language is literal: "building", "validating", "running", "completed",
  "failed". The backend phase vocabulary is mapped to these words in one place
  (`web/src/phaseLabel.js`) so every surface says the same thing.
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
