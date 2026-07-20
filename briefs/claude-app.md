# CLAUDE LANE — ProofBench server + report writer + web UI

You are the Claude lane building the app layer for ProofBench, an agentic tool-benchmarking
system (hackathon project, demo to judges). Work ONLY inside `D:/Daytona Hackathon/proofbench`
(your cwd).

MANDATORY FIRST: Read `CONTRACTS.md` in full. It is FROZEN. Match it exactly — especially
§10 (report_gen), §11 (server + SSE event schema), §12 (web).

## Your deliverables (you own these files — touch nothing else)

### 1. `engine/report_gen.py` (CONTRACTS §10)

`write_report(metrics: dict, citations: list[dict], out_path: str) -> str`.
- Kimi client: `openai` package, `base_url="https://api.moonshot.ai/v1"`,
  `api_key=os.environ["MOONSHOT_API_KEY"]`, model `os.environ.get("KIMI_MODEL", "kimi-k2-thinking")`.
- Prompt: ranked markdown report — summary table first (candidate, exact accuracy, F1, CER,
  latency, failure rate, cost/1k, setup complexity), then per-candidate findings, then a
  2-3 sentence verdict, then "Sources" list from citations. Explicitly forbid inventing
  numbers: only reformat the given metrics. Write file, return markdown.
- If the API call fails, return a plain markdown table built from the metrics dict (no LLM)
  so the pipeline never breaks. Log the failure reason on one line.

### 2. `server/runs.py` + `server/main.py` (CONTRACTS §11)

- `runs.py`: in-memory registry — sessions `{id: {id, title, phase, created_at, spec,
  results, queue}}` where `queue` is a `queue.Queue` of SSE events; helper `emit(session_id,
  event, data)`; persist each finished run's spec/metrics/report under `runs/<id>/`.
- `main.py`: FastAPI, CORS for `http://localhost:5173`. Endpoints exactly per §11.
  - SSE via `StreamingResponse(gen(), media_type="text/event-stream")`; generator drains the
    session queue, formats `event: X\ndata: {json}\n\n`, sends `: ping` every 15 s when idle,
    closes on a `done` event.
  - `POST /api/chat`: create/continue session; if the message looks like a benchmark
    request, call `engine.agent.Orchestrator.chat(...)` in a daemon thread, wiring
    `Orchestrator`'s `emit` to the session queue. Import lazily inside the endpoint so the
    server starts even if `engine/agent.py` is still being written by another lane (catch
    ImportError → emit a friendly `error` event + `done`).
  - `POST /api/datasets`: multipart (images[] + ground_truth file) saved under
    `data/uploads/<id>/`, OR `{"use_synthetic": true}` → subprocess
    `.venv/Scripts/python make_dataset.py --out data/demo --n 15` (fall back to `python`).
  - `POST /api/sessions/{id}/run` with `{spec}` → thread running
    `Orchestrator.run_benchmark(spec)`; on completion emit `artifact/results` (metrics),
    call `engine.report_gen.write_report`, emit `artifact/report`, then `done`.
  - `GET /api/sessions`, `GET /api/sessions/{id}`, `GET /api/runs/{id}/results` per §11.
- server starts with `uvicorn server.main:app --port 8000` from project root.

### 3. `web/` (CONTRACTS §12) — the demo face; make it genuinely impressive

Vite + React 18 + Tailwind v3, dark professional AI-startup chat UI (think Linear/ChatGPT-dark,
NOT a toy). Write all files directly (package.json, vite.config.js, tailwind.config.js,
postcss.config.js, index.html, src/**) — do NOT run interactive scaffolders.
- `src/api.js`: `postChat`, `uploadDataset`, `startRun`, `openEvents(sessionId)` (EventSource),
  `listSessions`, `getSession`, `getResults`; base `http://localhost:8000`.
- `src/components/Sidebar.jsx`: session list with phase status dots, "New benchmark" button.
- `src/components/ChatThread.jsx`: message list; markdown via `react-markdown`; assistant
  text streams token-by-token from `delta` events; auto-scroll.
- `src/components/Composer.jsx`: textarea (Enter to send), attach chip for dataset upload
  (images + CSV, drag-drop), "use synthetic demo set" chip.
- `src/components/SpecCard.jsx`: renders `artifact/spec` — candidate chips (removable),
  fields, dataset line; "Run benchmark" button → `startRun`.
- `src/components/AgentTraceCard.jsx`: renders `trace`/`sandbox_log`/`state` — tool-call
  feed with status icons; per-sandbox collapsible terminal panels (monospace, black bg)
  keyed by `sandbox` name; phase badges building/validating/running/done/failed; highlight
  repair attempts (amber) and failures (red).
- `src/components/ResultsCard.jsx`: renders `artifact/results` + `artifact/report` — ranked
  sortable table, per-field accuracy bars, verdict markdown, citation links, "Download
  report" (blob download of report markdown).
- `src/App.jsx`: composes Sidebar + ChatThread + Composer; wires EventSource lifecycle
  (open on session start, route events to state, close on done); loading/empty states.
- Styling: Tailwind dark (zinc-950 bg, zinc-800 borders, indigo→violet gradient accents,
  Inter from Google Fonts in index.html). No emojis in UI copy. Readable on a projector.

## Rules

- Match CONTRACTS.md exactly; do NOT modify it or other lanes' files (see ownership table).
- No secrets in code. Python 3.12; deps from `requirements.txt` only (fastapi, uvicorn,
  python-multipart, openai, requests).
- Web deps pinned to stable majors; keep the dependency list lean.

## Acceptance (ALL must pass before you finish)

1. `cd web && npm install && npm run build` → exits 0.
2. From project root: `.venv/Scripts/python -c "import server.main; print('server imports ok')"`
   (if `.venv` missing, wait 30 s and retry; else fall back to `python`).
3. `.venv/Scripts/python -c "from engine.report_gen import write_report; print('report_gen imports ok')"`.
4. Start the server briefly and `curl -s http://localhost:8000/api/sessions` returns `[]`,
   then kill it. (Use a background process + sleep 3.)

Finish by printing: files written + output of each acceptance command.
