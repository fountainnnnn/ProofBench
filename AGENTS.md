# AGENTS.md — ProofBench repo conventions

Read `CONTRACTS.md` (frozen interfaces) and `DESIGN.md` / `PRODUCT.md` (design system and
product context) before writing any code here.

## Stack

- Python 3.12 backend: FastAPI (`server/`), benchmark engine (`engine/`), venv at `.venv`
  (use `.venv/Scripts/python` on this Windows host). Deps only from `requirements.txt`.
- Frontend: Vite + React 18 + Tailwind v3 in `web/`. Light theme per `DESIGN.md`.
- Secrets live only in `.env` (never committed, never printed, never hardcoded).

## Commands

- Backend: `.venv/Scripts/python -m uvicorn server.main:app --port 8000` (from repo root)
- Frontend dev: `cd web && npm run dev` (port 5173)
- Tests: `.venv/Scripts/python -m pytest engine/test_evaluate.py -q`
- Offline pipeline check: `.venv/Scripts/python integration_test.py`
- Sponsor API check: `.venv/Scripts/python smoke_test.py`
- Web build: `cd web && npm run build`

## Rules for any agent (human or AI)

1. `CONTRACTS.md` is frozen. Do not change interfaces; report conflicts instead.
2. The deterministic evaluator (`engine/evaluate.py`) is the ONLY source of correctness
   scores. Never let an LLM judge extraction quality.
3. Sandbox-side code targets Debian Linux; host-side code must run on Windows.
4. SSE log lines: single-line, ≤300 chars, no secrets.
5. Frontend: follow `DESIGN.md` tokens and bans. No em dashes in UI copy, no emojis.
6. Keep diffs scoped. Match the surrounding file's style.
7. After changing behavior, update this file, `CONTRACTS.md`, or `README.md` if the
   change affects commands, layout, or conventions.

## Layout

```
engine/      orchestrator agent, tools, sandbox pool, evaluator, docs intel, adapters
server/      FastAPI app + session registry (SSE event log)
web/         React console (chat, runs, datasets, settings, landing)
data/        datasets (demo synthetic set + uploads)   [gitignored contents]
runs/        per-run artifacts (spec, results, metrics, report) [gitignored]
briefs/      task briefs used to dispatch build lanes
```
