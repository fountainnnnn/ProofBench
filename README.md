# ProofBench

**Agentic assessment of tools, built from their own docs.** Demo mode provides the
deterministic invoice-extraction walkthrough. Real mode accepts any company tool
category: an OpenAI or Kimi orchestrator discovers candidates, searches and scrapes
official documentation with Oxylabs, and assesses whether each integration can be
implemented credibly from that evidence. Daytona is created only for implementable
candidates. Candidates without sufficient implementation detail skip Daytona and still
receive an evidence-backed feasibility rating and report.

## Architecture

```
                        ┌────────────────────────────────────────────┐
                        │  Browser — React 18 + Vite + Tailwind      │
                        │  web/          http://localhost:5173       │
                        │  chat · spec editor · agent trace feed ·   │
                        │  per-sandbox terminals · results dashboard │
                        └───────────────────┬────────────────────────┘
                              REST + SSE    │  (CORS, /api/*)
                        ┌───────────────────▼────────────────────────┐
                        │  FastAPI — server/main.py, server/runs.py  │
                        │  /api/chat · /api/datasets · /api/sessions │
                        │  /api/sessions/:id/run · /events (SSE)     │
                        │                http://localhost:8000       │
                        └───────────────────┬────────────────────────┘
                                            │ one Orchestrator per session
                        ┌───────────────────▼────────────────────────┐
                        │  Orchestrator — engine/agent.py            │
                        │  Kimi tool-calling loop (≤40 tool calls)   │
                        │  INTAKE → SPEC_CONFIRM → DOCS_INTEL →      │
                        │  ADAPTER_GEN → PROVISIONING → BUILDING →   │
                        │  VALIDATING → RUNNING → COLLATING →        │
                        │  EVALUATING → REPORTING → DONE             │
                        └──┬─────────┬──────────┬──────────┬─────────┘
                           │         │          │          │
               ┌───────────▼─┐ ┌─────▼─────┐ ┌──▼────────┐ │
               │ docs_intel  │ │adapter_gen│ │sandbox_   │ │
               │  (Oxylabs)  │ │(Doubleword│ │pool       │ │
               │ search +    │ │ codegen)  │ │(Daytona)  │ │
               │ scrape docs │ │ adapter   │ │ 4 warm    │ │
               │ + pricing   │ │ from docs │ │ sandboxes │ │
               └─────────────┘ └───────────┘ └──┬────────┘ │
                                                │          │
                              ┌─────────────────▼────────┐ │
                              │ one sandbox per candidate│ │
                              │ build cmds → adapter →   │ │
                              │ extract(image) per doc   │ │
                              │ tesseract · easyocr ·    │ │
                              │ nosana_vlm · doubleword  │ │
                              │ · discovered tools       │ │
                              └─────────────────┬────────┘ │
                      RESULT_JSON per doc       │          │
                              ┌─────────────────▼──────────▼────────┐
                              │  evaluate.py — DETERMINISTIC        │
                              │  results.jsonl vs ground_truth.csv  │
                              │  exact accuracy · field F1 · CER ·  │
                              │  latency · failure rate · cost      │
                              │  (no LLM, no network, no judging)   │
                              └─────────────────┬───────────────────┘
                              ┌─────────────────▼───────────────────┐
                              │  report_gen.py — Kimi reformats the │
                              │  metrics into a ranked markdown     │
                              │  report + citations (never invents  │
                              │  numbers) → SSE → web, runs/<id>/   │
                              └─────────────────────────────────────┘
```

## Sponsor mapping

| Sponsor | Role in ProofBench |
|---|---|
| **Daytona** | Sandbox fleet — a pre-warmed pool of isolated sandboxes; one per candidate for building, validating, and running its integration in parallel (`engine/sandbox_pool.py`). |
| **Kimi (Moonshot)** | Orchestrator — runs the intake/discovery chat and the autonomous tool-calling benchmark loop (`engine/agent.py`); also writes the final ranked report (`engine/report_gen.py`). |
| **Oxylabs** | Search + scrape — Google search for candidate discovery and scraping of documentation/pricing pages (`engine/docs_intel.py`). |
| **Nosana** | GPU VLM candidate — a hosted OpenAI-compatible vision model benchmarked as a candidate alongside local OCR tools (`engine/candidates/fallbacks/nosana_vlm.py`). |
| **Doubleword** | Batched assessment + candidate — the autobatcher evaluates all Real-mode candidate documentation with the configured DeepSeek V4 Pro model, and Doubleword also serves as a hosted vision candidate in the OCR walkthrough. |

## Setup

Prerequisites: Python 3.12, Node.js 18+.

```bash
# 1. Python virtual environment
python -m venv .venv

# 2. Install backend dependencies (Windows Git Bash)
.venv/Scripts/python -m pip install -r requirements.txt
#    macOS/Linux: .venv/bin/python -m pip install -r requirements.txt

# 3. Configure API keys
cp .env.example .env
#    open .env and fill in the values — every variable is documented
#    inline in .env.example

# 4. Install frontend dependencies
cd web && npm install && cd ..
```

## Run

Chat sessions are stored under `runs/`. Use the **Delete** control beside an idle session in the console to permanently remove its chat history and run artifacts. Stop running sessions before deleting them.

Two terminals from the project root:

```bash
# Terminal 1 — API server on :8000
.venv/Scripts/python -m uvicorn server.main:app --port 8000

# Terminal 2 — web UI on :5173
cd web && npm run dev
```

Open http://localhost:5173.

### Adding provider credentials in Settings

System credentials in `.env` are available automatically. You can also open **Settings**
and add a provider's required environment variable, such as `ANTHROPIC_API_KEY` or
`MISTRAL_API_KEY`. Settings-provided values live only in the running server process,
are never returned to the browser after saving, and are injected only into the Daytona
sandbox processes that execute generated adapters. Restarting the server clears them.

## Demo flow (6 steps)

1. **Start a session.** Open http://localhost:5173 and click "New benchmark" in
   the sidebar.
2. **Say what to benchmark.** In the chat, e.g. "Benchmark invoice-extraction
   tools on my invoices." The Kimi intake agent searches the web and scrapes
   docs (Oxylabs), then proposes an editable spec — candidates such as
   `tesseract`, `easyocr`, `nosana_vlm`, `doubleword`.
3. **Attach a dataset.** Click the synthetic-set chip to generate 15 labelled
   invoices (`make_dataset.py`), or upload your own images plus a
   `ground_truth.csv`.
4. **Confirm and run.** Review the spec card (edit candidate chips if needed)
   and hit "Run benchmark".
5. **Watch the agent work.** The trace feed streams live: docs scraping,
   adapter codegen (Doubleword), four Daytona sandboxes building, validating,
   and running candidates in parallel — with per-sandbox terminal output and
   highlighted self-repair attempts.
6. **Read the verdict.** The results card shows the deterministic metrics per
   candidate (exact accuracy, field F1, CER, mean latency, failure rate, cost
   per 1k docs, setup complexity) in a sortable ranked table, plus a
   citation-backed markdown report you can download.

## Real mode flow

1. Select **Real** and describe the company objective and tools to compare. The category
   can be CRM, observability, collaboration, payments, data infrastructure, or another
   software category.
2. The intake agent searches for official candidate documentation and produces an editable
   implementation-assessment spec with citations.
3. ProofBench submits the scraped candidate docs through the Doubleword autobatcher using
   the configured `DOUBLEWORD_MODEL`, then rates documentation quality, implementation
   feasibility, authentication clarity, and setup complexity.
4. When the documentation supports a credible integration, ProofBench generates a smoke test
   and validates it in Daytona. When it does not, Daytona is skipped for that candidate.
5. Every candidate receives a 0-100 rating and appears in the final markdown and PDF report,
   including candidates that could not be implemented.

## Verify the wiring

```bash
# Sponsor API connectivity — prints PASS/SKIP/FAIL per service, never secrets
.venv/Scripts/python smoke_test.py

# Deterministic evaluator unit tests (no network, no API keys needed)
.venv/Scripts/python -m pytest engine/test_evaluate.py -q

# Regenerate the synthetic demo dataset
.venv/Scripts/python make_dataset.py --out data/demo --n 15
```

## Repo layout

| Path | What lives there |
|---|---|
| `engine/` | Orchestrator, tool layer, sandbox pool, docs intel, adapter codegen, deterministic evaluator, report writer, candidate definitions |
| `server/` | FastAPI app — chat, datasets, run registry, SSE event stream |
| `web/` | React 18 + Vite + Tailwind v3 frontend (dark theme) |
| `data/` | Generated/uploaded datasets (`images/` + `ground_truth.csv`) |
| `runs/` | Per-run artifacts: `results.jsonl`, metrics, reports |
| `make_dataset.py` | Synthetic labelled invoice generator (Pillow, deterministic seed) |
| `smoke_test.py` | Sponsor connectivity check (Daytona, Kimi, Doubleword, Nosana, Oxylabs) |
| `CONTRACTS.md` | Frozen engineering contracts — interfaces every lane builds against |
| `slides/` | Hackathon pitch deck: `index.html` (browser deck) and `make_pptx.py`, which builds `proofbench_pitch.pptx` |
