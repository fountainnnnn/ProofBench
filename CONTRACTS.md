# ProofBench — Engineering Contracts (FROZEN)

Every lane builds against this file. Do not deviate from these interfaces. If a contract
is impossible to satisfy, stop and report — do not improvise a different shape.

Python 3.12. All LLM clients use the `openai` package with a custom `base_url`.
All secrets come from `.env` via `python-dotenv` (`load_dotenv()` at entry points only).
Never hardcode credentials. Never print secret values.

Project layout (ownership):

| Path | Owner |
|---|---|
| `CONTRACTS.md`, `smoke_test.py`, `engine/candidates/base.py`, `engine/sandbox_pool.py`, `engine/tools.py`, `engine/agent.py` | KIMI (orchestrator) |
| `make_dataset.py`, `engine/evaluate.py`, `engine/test_evaluate.py`, `engine/docs_intel.py`, `engine/adapter_gen.py`, `engine/candidates/fallbacks/*.py` | CODEX |
| `server/main.py`, `server/runs.py`, `engine/report_gen.py`, `web/**` | CLAUDE |

---

## 1. Candidate contract — `engine/candidates/base.py`

Every benchmark candidate (Tesseract, EasyOCR, hosted VLMs, discovered tools) is described
by the SAME dataclass. The runner is fully generic over it.

```python
from dataclasses import dataclass, field

@dataclass
class Candidate:
    name: str                    # unique slug, e.g. "tesseract"
    display_name: str            # "Tesseract OCR 5.x"
    docs_url: str                # documentation the integration was built from
    kind: str                    # "local_tool" | "hosted_api"
    build_commands: list[str]    # shell cmds to install/configure INSIDE a Daytona sandbox
    adapter_code: str            # python source executed INSIDE the sandbox (see below)
    setup_complexity: int = 1    # 1 (trivial) .. 5 (painful); agent may worsen it on repairs
    pricing_url: str = ""        # where pricing was scraped from ("" if free/local)
```

`adapter_code` contract (runs inside the sandbox via `code_run`, CWD contains the dataset):
- Reads ONE image path from `sys.argv`... NO — simpler: adapter defines
  `extract(image_path: str) -> dict` and the runner wraps it. The adapter MUST end with:
  ```python
  import json, sys, time
  _t0 = time.time()
  try:
      _out = extract(sys.argv[1])
      print("RESULT_JSON:" + json.dumps({"ok": True, "fields": _out, "latency_s": round(time.time()-_t0, 3)}))
  except Exception as e:
      print("RESULT_JSON:" + json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}))
  ```
- `extract()` returns `{"invoice_number": str, "date": str, "vendor": str, "total": str}`
  (empty string for missing fields). No prints other than the `RESULT_JSON:` line.

## 2. Data schemas

**Ground truth CSV** (`ground_truth.csv`):
```csv
doc_id,invoice_number,date,vendor,total
inv_001,INV-1001,2026-06-01,Acme Pte Ltd,128.50
```
`date` is ISO `YYYY-MM-DD`; `total` is decimal string, SGD.

**Results JSONL** (one line per candidate × document):
```json
{"candidate": "tesseract", "doc_id": "inv_001", "ok": true,
 "prediction": {"invoice_number": "...", "date": "...", "vendor": "...", "total": "..."},
 "latency_s": 1.234, "error": null}
```
On failure: `"ok": false, "prediction": null, "error": "TimeoutError: ..."`, `latency_s` measured anyway.

**Metrics JSON** (evaluator output, per candidate):
```json
{"tesseract": {"exact_accuracy": 0.73, "field_f1": 0.81, "cer": 0.12,
 "mean_latency_s": 1.9, "failure_rate": 0.0, "cost_per_1k_docs": 0.0,
 "setup_complexity": 2, "n_docs": 15}}
```

## 3. `make_dataset.py` (CODEX)

CLI: `python make_dataset.py --out data/demo --n 15`
- Generates `n` synthetic invoice PNGs (Pillow only, no external fonts/assets — use
  `ImageFont.load_default()` or truetype fallback) into `<out>/images/inv_XXX.png`
- Varied but deterministic layouts (`random.seed(42)`): 3+ layout templates, slight noise/rotation
- Writes `<out>/ground_truth.csv` per §2. Printed output: absolute path of the CSV.
- Acceptance: `python make_dataset.py --out data/demo --n 15` exits 0, CSV has 15 rows + header.

## 4. `engine/evaluate.py` (CODEX) — deterministic, NO LLM, NO network

```python
def normalize_text(s: str) -> str
def normalize_date(s: str) -> str        # best-effort → "YYYY-MM-DD"; "" if unparseable
def normalize_amount(s: str) -> int|None # → cents; None if unparseable
def token_f1(pred: str, gt: str) -> float
def cer(pred: str, gt: str) -> float     # pure-python Levenshtein / max(len(gt),1)
def evaluate_results(results_path: str, ground_truth_path: str,
                     pricing: dict | None = None) -> dict  # → Metrics JSON (§2)
```
- Exact-match rules: text fields compared after `normalize_text`; date after `normalize_date`;
  total after `normalize_amount` (equal cents).
- `cost_per_1k_docs` = per-doc usage × price from `pricing` (name → per-doc or per-1k-token
  price); 0.0 for local tools when `pricing` lacks an entry.
- `engine/test_evaluate.py`: ≥10 pytest cases incl. date formats ("1 Jun 2026", "06/01/2026"),
  currency ("$1,234.50"), partial vendor match (F1 in (0,1)), CER edge cases (empty gt → 0).
- Acceptance: `python -m pytest engine/test_evaluate.py -q` all green.

## 5. `engine/docs_intel.py` (CODEX) — Oxylabs

Endpoint `https://realtime.oxylabs.io/v1/queries`, HTTP basic auth from
`OXYLABS_USERNAME`/`OXYLABS_PASSWORD`, `requests`, 60 s timeout, raise `RuntimeError` with
response body on non-200.

```python
def web_search(query: str, n: int = 5) -> list[dict]
    # payload {"source":"google_search","query":query,"parse":True}
    # → [{"title","url","snippet"}] from results[0].content.results.organic[:n]
def scrape_page(url: str) -> str
    # payload {"source":"universal","url":url} → results[0].content (str, may be HTML)
def gather_tool_docs(candidates: list[dict], out_dir: str) -> dict
    # candidates: [{"name","docs_url","pricing_url"}]
    # writes <out_dir>/docs/<name>.md (+ <name>_pricing.md) ; returns
    # {"docs": {name: path}, "pricing": {name: path_or_none}}
```

## 6. `engine/adapter_gen.py` (CODEX)

```python
def generate_adapter(tool_name: str, docs_md: str, model: str | None = None) -> Candidate
def get_fallback(name: str) -> Candidate | None
```
- `generate_adapter`: OpenAI-compatible client, `base_url="https://api.doubleword.ai/v1"`,
  key `DOUBLEWORD_API_KEY`, model = arg or `DOUBLEWORD_MODEL` env. Prompt demands STRICT JSON
  `{"display_name","build_commands":["..."],"adapter_code":"...","setup_complexity":N}`
  where adapter_code follows §1. Parse defensively (strip ```json fences); raise `ValueError`
  on bad output. Build the returned `Candidate` with `docs_url=""`, `kind` guessed from docs.
- `get_fallback` imports `engine/candidates/fallbacks/*` — one function per file:
  `def candidate() -> Candidate` (per §1) for `tesseract`, `easyocr`, `nosana_vlm`, `doubleword`.
- Hosted-API fallbacks (nosana_vlm, doubleword): `build_commands` = `["pip install openai pillow"]`
  (run in sandbox); adapter posts base64 image to the OpenAI-compatible endpoint using env
  vars baked in at generation time by `tools.py` — adapters read `os.environ[...]`.
  nosana_vlm uses `NOSANA_BASE_URL`/`NOSANA_API_KEY`/`NOSANA_MODEL`; doubleword uses
  `DOUBLEWORD_BASE_URL`(default https://api.doubleword.ai/v1)/`DOUBLEWORD_API_KEY`/`DOUBLEWORD_MODEL`.
- Acceptance: `python -c "from engine.adapter_gen import get_fallback; print(get_fallback('tesseract').name)"` → `tesseract`.

## 7. `engine/sandbox_pool.py` (KIMI) — Daytona lifecycle

```python
class SandboxHandle:  # wraps a daytona sandbox
    id: str; label: str
class SandboxPool:
    def __init__(self, size: int = 4)
    def start(self) -> None                 # pre-warm `size` sandboxes (parallel)
    def acquire(self, label: str) -> SandboxHandle
    def exec(self, h: SandboxHandle, cmd: str, timeout: int = 120) -> str      # stdout+stderr
    def run_python(self, h: SandboxHandle, code: str, timeout: int = 180) -> str  # stdout
    def upload(self, h: SandboxHandle, local_path: str, remote_path: str) -> None
    def release(self, h: SandboxHandle) -> None   # back to pool (keep alive)
    def destroy_all(self) -> None
```
Uses `daytona` SDK, `Daytona()` from env. Daytona API key: `DAYTONA_API_KEY`.

## 8. `engine/tools.py` (KIMI) — agent tool layer

- `TOOL_SCHEMAS: list[dict]` — OpenAI function-calling schemas for:
  `web_search(query)`, `scrape_docs(url)`, `generate_adapter(tool_name, docs_md)`,
  `spawn_sandbox(label)`, `exec_in_sandbox(id, cmd)`, `run_python_in_sandbox(id, code)`,
  `upload_files(id, local_dir)`, `record_result(candidate, doc_id, ok, prediction, latency_s, error)`,
  `evaluate(results_path, ground_truth_path)`, `write_report(metrics_json)`
- `dispatch_tool(name: str, args: dict, ctx: RunContext) -> str` (JSON string result).
- `RunContext` dataclass: `run_id`, `pool: SandboxPool`, `run_dir: str`,
  `emit: Callable[[str, dict], None]` (event emitter → SSE), `results_path`, `env_passthrough: dict`
  (secrets injected into sandbox adapter env, never logged).

## 9. `engine/agent.py` (KIMI) — Orchestrator

```python
class Orchestrator:
    def __init__(self, run_id: str, run_dir: str, emit: Callable[[str, dict], None])
    def chat(self, user_message: str) -> None      # INTAKE/DISCOVERY conversation (streams)
    def run_benchmark(self, spec: dict) -> dict    # autonomous protocol → metrics dict
```
- Kimi client: `base_url="https://api.moonshot.ai/v1"`, key `MOONSHOT_API_KEY`,
  model `KIMI_MODEL` env (default `kimi-k2-thinking`).
- Tool loop: ≤40 calls; per-call 120 s default; 1 adapter self-repair per candidate, then
  `get_fallback(name)`; on total candidate failure mark it and CONTINUE others.
- Phases (emitted as `state` events): INTAKE → SPEC_CONFIRM → DOCS_INTEL → ADAPTER_GEN →
  PROVISIONING → BUILDING → VALIDATING → RUNNING → COLLATING → EVALUATING → REPORTING → DONE.
- `spec` = `{"category": str, "fields": [...4 default...], "candidates": [{"name","docs_url",
  "pricing_url","kind"}], "dataset": {"path": str}}`.
- Never asks the LLM to judge correctness. Evaluation only via `evaluate` tool.

## 10. `engine/report_gen.py` (CLAUDE)

```python
def write_report(metrics: dict, citations: list[dict], out_path: str) -> str  # markdown
```
Kimi client (same env as §9). Input = evaluator metrics JSON + `[{"title","url"}]` from
docs_intel. Output: ranked markdown report — table first, per-candidate findings, verdict,
citations. MUST NOT invent numbers; only reformat what's given. Writes file, returns markdown.

## 11. Server — `server/main.py`, `server/runs.py` (CLAUDE)

FastAPI on :8000, CORS `http://localhost:5173`. In-memory registry + persist under `runs/<id>/`.

- `POST /api/chat` `{session_id?: str, message: str, dataset_id?: str}` →
  `{session_id}` immediately; agent reply streams on the session's SSE channel.
- `POST /api/datasets` multipart images[]+ground_truth.csv, or `{use_synthetic: true}`
  (runs `make_dataset.py --n 15`) → `{dataset_id, path}`.
- `POST /api/sessions/{id}/run` `{spec}` → launches `Orchestrator.run_benchmark` in a thread.
- `GET /api/sessions/{id}/events` → SSE (`text/event-stream`).
- `GET /api/sessions` → `[{id, title, phase, created_at}]`. `GET /api/sessions/{id}` → full state.
- `GET /api/runs/{id}/results` → `{metrics, report_md, citations}`.

**SSE event schema (FROZEN):**
```
event: delta      data: {"text": "..."}                                  # assistant tokens
event: artifact   data: {"kind": "spec", "spec": {...}}
event: artifact   data: {"kind": "trace", "tool": "...", "args_summary": "...", "status": "start|ok|error", "detail": "..."}
event: artifact   data: {"kind": "sandbox_log", "sandbox": "tesseract", "line": "...", "phase": "building|validating|running"}
event: artifact   data: {"kind": "results", "metrics": {...}}
event: artifact   data: {"kind": "report", "markdown": "...", "citations": [...]}
event: state      data: {"phase": "BUILDING", "candidates": {"tesseract": "running", ...}}
event: error      data: {"message": "..."}
event: done       data: {}
```

## 12. Web — `web/` (CLAUDE) — Vite + React 18 + Tailwind v3, dark theme

Professional AI-startup chat UI (think Linear/ChatGPT dark). Components in `web/src/components/`:
`Sidebar` (session list, "New benchmark"), `ChatThread` (messages, markdown, streaming),
`Composer` (input, dataset attach chip, synthetic-set chip), `SpecCard` (editable candidate
chips + "Run benchmark" button → POST run), `AgentTraceCard` (tool feed + per-sandbox
terminal panels, phase badges, self-repair highlight), `ResultsCard` (ranked sortable table
+ per-field bars + verdict + citations + report download).
`web/src/api.js`: `postChat`, `uploadDataset`, `startRun`, `openEvents(session_id)` (EventSource),
`listSessions`, `getSession`, `getResults`. Base URL `http://localhost:8000`, vite proxy optional.
Font: Inter via Google Fonts. Accent: indigo/violet gradient. No emojis in UI copy.
Acceptance: `cd web && npm install && npm run build` exits 0.

## 13. `smoke_test.py` (KIMI)

`python smoke_test.py` — checks Daytona (create/exec/code_run/apt tesseract), Kimi (chat +
`/v1/models`), Doubleword (`/v1/models`), Nosana (auth), Oxylabs (search+scrape). Prints
PASS/FAIL per service, exits non-zero on any FAIL. Never prints secrets.

## 14. Global rules

- stdlib + `requirements.txt` only. No new deps without orchestrator approval.
- Every module runnable on Windows host; sandbox-side code targets Debian Linux.
- No network calls in `evaluate.py`. No LLM judging of extraction correctness anywhere.
- Log lines destined for SSE must be single-line, ≤300 chars, no secrets.
