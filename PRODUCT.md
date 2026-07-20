# PRODUCT.md — ProofBench

## Users

Hackathon judges and technical buyers watching a live demo, then developers evaluating the
product afterwards on their own laptop. Both are fluent in modern dev tools (Linear, Vercel,
Stripe dashboards). They notice when a component behaves strangely. They do not read marketing
copy twice.

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

A judge watches the demo on a projector in a bright NUS seminar room at 4:30 PM, Singapore
daylight washing over the screen. The presenter stands five meters from the display. Every
status badge, table row, and log line must survive washed-out projection. Light theme, high
contrast, nothing that relies on subtle darkness.

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
4. Demo legibility beats designer cleverness, always.
