# CODEX LANE — ProofBench backend modules

You are the Codex lane building backend modules for ProofBench, an agentic tool-benchmarking
system (hackathon project). Work ONLY inside `D:/Daytona Hackathon/proofbench` (your cwd).

MANDATORY FIRST: Read `CONTRACTS.md` in full. It is FROZEN. Your modules must match it exactly.

## Your deliverables (you own these files — touch nothing else)

1. `make_dataset.py` — per CONTRACTS §3. Synthetic invoice PNGs via Pillow, deterministic
   (`random.seed(42)`), ≥3 layout templates, slight rotation/noise for realism, writes
   `ground_truth.csv`. CLI: `--out`, `--n`. Must run on Windows, Pillow only, no external fonts
   (`ImageFont.load_default()` is fine; vary sizes via `font_variant` if available, else draw
   text at different offsets — do NOT download fonts).
2. `engine/evaluate.py` — per CONTRACTS §4. Deterministic metrics. Pure stdlib. No network,
   no LLM. `evaluate_results()` returns the Metrics JSON shape from §2. `pricing` param maps
   candidate name → per-doc cost in USD (0.0 default).
3. `engine/test_evaluate.py` — per §4: ≥10 pytest cases (date formats, currency, partial
   matches, CER edge cases, a full tiny end-to-end evaluate_results on a 2-doc fixture
   written to tmp_path).
4. `engine/docs_intel.py` — per CONTRACTS §5. Oxylabs realtime API. `web_search` uses
   payload `{"source": "google_search", "query": q, "parse": True}` and parses
   `results[0]["content"]["results"]["organic"]` — but parse defensively (content may be a
   JSON string; if so `json.loads` it first). `scrape_page` uses `{"source":"universal","url":u}`.
   Basic auth from `OXYLABS_USERNAME`/`OXYLABS_PASSWORD` env (raise clear error if missing).
5. `engine/adapter_gen.py` — per CONTRACTS §6. `generate_adapter()` via OpenAI-compatible
   client at `https://api.doubleword.ai/v1` (`DOUBLEWORD_API_KEY`, model arg or
   `DOUBLEWORD_MODEL` env). Defensive JSON parsing of the LLM output (strip fences, find
   outermost braces). `get_fallback()` imports the four modules below lazily.
6. `engine/candidates/fallbacks/tesseract.py`, `easyocr.py`, `nosana_vlm.py`, `doubleword.py`
   — each defines `candidate() -> Candidate` (import `Candidate` from `engine.candidates.base`;
   CONTRACTS §1). Requirements:
   - **tesseract**: kind="local_tool", docs_url="https://tesseract-ocr.github.io/tessdoc/",
     build_commands install tesseract via apt (`apt-get update && apt-get install -y
     tesseract-ocr python3-pip` then `pip install pytesseract pillow`), adapter uses
     pytesseract image_to_string + regex/heuristic field extraction (invoice number
     patterns like INV-/No., date patterns, "Total" lines, vendor = first non-empty line).
   - **easyocr**: kind="local_tool", docs_url="https://github.com/JaidedAI/EasyOCR",
     build_commands `pip install easyocr opencv-python-headless` (note: CPU mode),
     adapter runs easyocr.Reader(['en'], gpu=False), same heuristics on the joined text.
   - **nosana_vlm**: kind="hosted_api", docs_url="https://docs.nosana.com", build_commands
     `pip install openai pillow`; adapter posts the base64 image to an OpenAI-compatible
     chat-completions endpoint using `os.environ["NOSANA_BASE_URL"]`,
     `os.environ["NOSANA_API_KEY"]`, `os.environ["NOSANA_MODEL"]`; system prompt demands
     STRICT JSON with the 4 fields; adapter extracts the JSON object from the reply
     (find outermost braces).
   - **doubleword**: same shape as nosana_vlm but env vars `DOUBLEWORD_BASE_URL`
     (default `https://api.doubleword.ai/v1`), `DOUBLEWORD_API_KEY`, `DOUBLEWORD_MODEL`;
     docs_url="https://docs.doubleword.ai".
   - Every adapter MUST end with the exact RESULT_JSON wrapper from CONTRACTS §1.
   - setup_complexity: tesseract=2, easyocr=3, nosana_vlm=2, doubleword=2.

## Rules

- Python 3.12, stdlib + packages in `requirements.txt` only (`requests`, `openai`, `pillow`).
- No secrets in code or prints. Read env at call time, not import time.
- Type hints, concise docstrings. No emojis anywhere.
- Do NOT modify CONTRACTS.md or files owned by other lanes (see ownership table).
- Run acceptance with the project venv: `.venv/Scripts/python` (if it doesn't exist yet,
  wait 30 s and retry — it's being created in parallel; fall back to `python` only if
  `.venv` never appears).

## Acceptance (ALL must pass before you finish)

1. `.venv/Scripts/python make_dataset.py --out data/demo --n 15` → exits 0; prints CSV path;
   `data/demo/ground_truth.csv` has 16 lines; 15 PNGs in `data/demo/images/`.
2. `.venv/Scripts/python -m pytest engine/test_evaluate.py -q` → all green.
3. `.venv/Scripts/python -c "from engine.adapter_gen import get_fallback; c=get_fallback('tesseract'); print(c.name, c.kind); assert 'RESULT_JSON' in c.adapter_code"` → prints `tesseract local_tool`.
4. `.venv/Scripts/python -c "from engine.docs_intel import web_search, scrape_page, gather_tool_docs; print('imports ok')"`.

Finish by printing: list of files written, and the output of each acceptance command.
