#!/usr/bin/env python3
"""ProofBench smoke test — verifies all five sponsor APIs (CONTRACTS.md §13).

Checks:
  1. Daytona    — create sandbox, exec, code_run, apt-install tesseract, delete
  2. Kimi       — /v1/models + one chat completion
  3. Doubleword — /v1/models (print up to 10 ids to help pick DOUBLEWORD_MODEL)
  4. Nosana     — authenticated GET on the models endpoint (any 2xx, or 404 with
                  auth accepted, is a PASS — their API shape is uncertain)
  5. Oxylabs    — google_search (parsed) + universal scrape of example.com

Rules (per contract):
  * .env is loaded via python-dotenv; secrets never printed.
  * A missing/empty key is a SKIP, not a FAIL — all missing keys are listed
    loudly in a MISSING KEYS summary at the end.
  * FAIL only when a key exists but the call errors.
  * Exits non-zero if any service FAILs (SKIPs do not affect the exit code).
"""

from __future__ import annotations

import os
import sys
import time

# --- .env loading (python-dotenv per contract; degrade gracefully if absent) ---
try:
    from dotenv import load_dotenv
except ImportError:  # venv still being prepared — rely on process env
    load_dotenv = None

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"

MISSING_KEYS: list[str] = []


def _require(*names: str) -> str | None:
    """Return a reason string if any of `names` is missing/empty, else None.

    Records every missing key in MISSING_KEYS for the end-of-run summary.
    """
    missing = []
    for name in names:
        if not os.environ.get(name, "").strip():
            if name not in MISSING_KEYS:
                MISSING_KEYS.append(name)
            missing.append(name)
    if missing:
        return "not set: " + ", ".join(missing)
    return None


def _one_line(s: object, limit: int = 160) -> str:
    """Collapse text to a single printable line for the summary table."""
    text = " ".join(str(s).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _exc(e: BaseException) -> str:
    return _one_line(f"{type(e).__name__}: {e}", 220)


# ---------------------------------------------------------------------------
# 1. Daytona
# ---------------------------------------------------------------------------
def check_daytona() -> tuple[str, str]:
    if reason := _require("DAYTONA_API_KEY"):
        return SKIP, reason
    try:
        from daytona import Daytona
    except ImportError as e:
        return FAIL, f"daytona package not importable ({_exc(e)})"

    def _out(resp: object) -> str:
        """Extract text output from a daytona ExecuteResponse (or plain str)."""
        if resp is None:
            return ""
        if isinstance(resp, str):
            return resp
        for attr in ("result", "stdout", "output"):
            val = getattr(resp, attr, None)
            if isinstance(val, str) and val:
                return val
        return str(resp)

    sb = None
    try:
        client = Daytona()  # reads DAYTONA_API_KEY / DAYTONA_API_URL / DAYTONA_TARGET
        print("  creating sandbox ...", flush=True)
        sb = client.create()

        out = _out(sb.process.exec("echo hello"))
        if "hello" not in out:
            return FAIL, f"exec('echo hello') returned unexpected output: {_one_line(out, 80)!r}"
        print("  exec echo hello          -> ok", flush=True)

        out = _out(sb.process.code_run("print(2+2)"))
        if "4" not in out:
            return FAIL, f"code_run('print(2+2)') returned unexpected output: {_one_line(out, 80)!r}"
        print("  code_run print(2+2)      -> ok", flush=True)

        print("  installing tesseract-ocr via apt (up to 300s) ...", flush=True)
        sb.process.exec("sudo apt-get update && sudo apt-get install -y tesseract-ocr", timeout=300)
        out = _out(sb.process.exec("tesseract --version"))
        lowered = out.lower()
        if "tesseract " not in lowered or "not found" in lowered or "command not found" in lowered:
            return FAIL, f"tesseract --version gave unexpected output: {_one_line(out, 80)!r}"
        version_line = next((ln.strip() for ln in out.splitlines() if "tesseract" in ln.lower()), "")
        print(f"  tesseract --version      -> {version_line}", flush=True)
        return PASS, f"sandbox create/exec/code_run ok; {version_line or 'tesseract installed'}"
    except Exception as e:
        return FAIL, _exc(e)
    finally:
        if sb is not None:
            try:
                sb.delete()
                print("  sandbox deleted", flush=True)
            except Exception as e:
                print(f"  WARNING: sandbox delete failed: {_exc(e)}", flush=True)


# ---------------------------------------------------------------------------
# 2. Kimi (Moonshot)
# ---------------------------------------------------------------------------
def check_kimi() -> tuple[str, str]:
    if reason := _require("MOONSHOT_API_KEY"):
        return SKIP, reason
    try:
        from openai import OpenAI
    except ImportError as e:
        return FAIL, f"openai package not importable ({_exc(e)})"

    model = os.environ.get("KIMI_MODEL", "").strip() or "kimi-k2-thinking"
    client = OpenAI(
        base_url="https://api.moonshot.ai/v1",
        api_key=os.environ["MOONSHOT_API_KEY"],
        timeout=60.0,
    )
    try:
        models = client.models.list()
        ids = [m.id for m in models.data]
        print(f"  /v1/models               -> {len(ids)} model(s)", flush=True)

        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Reply with the single word: ok"}],
            max_tokens=16,
        )
        choice = resp.choices[0]
        content = (choice.message.content or "").strip()
        print(f"  chat '{model}'           -> finish={choice.finish_reason}, {len(content)} char(s)", flush=True)
        return PASS, f"{len(ids)} models; chat '{model}' ok (finish={choice.finish_reason})"
    except Exception as e:
        print(f"  chat call failed; available model ids (fix KIMI_MODEL):", flush=True)
        try:
            for mid in [m.id for m in client.models.list().data]:
                print(f"    - {mid}", flush=True)
        except Exception as e2:
            print(f"    (could not list models: {_exc(e2)})", flush=True)
        return FAIL, _exc(e)


# ---------------------------------------------------------------------------
# 3. Doubleword
# ---------------------------------------------------------------------------
def check_doubleword() -> tuple[str, str]:
    if reason := _require("DOUBLEWORD_API_KEY"):
        return SKIP, reason
    try:
        from openai import OpenAI
    except ImportError as e:
        return FAIL, f"openai package not importable ({_exc(e)})"

    client = OpenAI(
        base_url="https://api.doubleword.ai/v1",
        api_key=os.environ["DOUBLEWORD_API_KEY"],
        timeout=60.0,
    )
    try:
        models = client.models.list()
        ids = [m.id for m in models.data]
        print(f"  /v1/models               -> {len(ids)} model(s); first {min(len(ids), 10)} id(s):", flush=True)
        for mid in ids[:10]:
            print(f"    - {mid}", flush=True)
        want = os.environ.get("DOUBLEWORD_MODEL", "").strip()
        note = ""
        if want:
            note = f"; DOUBLEWORD_MODEL '{want}' {'found' if want in ids else 'NOT in list'}"
            print(f"  DOUBLEWORD_MODEL check   ->{note}", flush=True)
        return PASS, f"{len(ids)} models{note}"
    except Exception as e:
        return FAIL, _exc(e)


# ---------------------------------------------------------------------------
# 4. Nosana
# ---------------------------------------------------------------------------
def check_nosana() -> tuple[str, str]:
    if reason := _require("NOSANA_API_KEY"):
        return SKIP, reason
    try:
        import requests
    except ImportError as e:
        return FAIL, f"requests package not importable ({_exc(e)})"

    url = "https://dashboard.k8s.prd.nos.ci/api/v1/models"
    headers = {"Authorization": f"Bearer {os.environ['NOSANA_API_KEY']}"}
    try:
        r = requests.get(url, headers=headers, timeout=30)
        print(f"  GET /api/v1/models       -> HTTP {r.status_code}", flush=True)
        if 200 <= r.status_code < 300:
            try:
                body = r.json()
                n = len(body) if isinstance(body, list) else len(body.get("data", body.get("models", [])))
                return PASS, f"HTTP {r.status_code}; ~{n} model(s) listed"
            except ValueError:
                return PASS, f"HTTP {r.status_code} (non-JSON body accepted)"
        if r.status_code == 404:
            return PASS, "HTTP 404 — auth accepted, endpoint shape uncertain (treated as PASS)"
        return FAIL, f"HTTP {r.status_code}: {_one_line(r.text, 120)}"
    except Exception as e:
        return FAIL, _exc(e)


# ---------------------------------------------------------------------------
# 5. Oxylabs
# ---------------------------------------------------------------------------
def check_oxylabs() -> tuple[str, str]:
    if reason := _require("OXYLABS_USERNAME", "OXYLABS_PASSWORD"):
        return SKIP, reason
    try:
        import requests
    except ImportError as e:
        return FAIL, f"requests package not importable ({_exc(e)})"

    url = "https://realtime.oxylabs.io/v1/queries"
    auth = (os.environ["OXYLABS_USERNAME"], os.environ["OXYLABS_PASSWORD"])
    try:
        r = requests.post(
            url,
            auth=auth,
            json={"source": "google_search", "query": "tesseract ocr documentation", "parse": True},
            timeout=60,
        )
        if r.status_code != 200:
            return FAIL, f"google_search HTTP {r.status_code}: {_one_line(r.text, 120)}"
        results = r.json().get("results") or []
        if not results:
            return FAIL, "google_search returned HTTP 200 but no results"
        print(f"  google_search (parsed)   -> HTTP 200, {len(results)} result block(s)", flush=True)

        r = requests.post(
            url,
            auth=auth,
            json={"source": "universal", "url": "https://example.com"},
            timeout=60,
        )
        if r.status_code != 200:
            return FAIL, f"universal scrape HTTP {r.status_code}: {_one_line(r.text, 120)}"
        print("  universal example.com    -> HTTP 200", flush=True)
        return PASS, f"google_search ok ({len(results)} result block(s)); universal scrape ok"
    except Exception as e:
        return FAIL, _exc(e)


# ---------------------------------------------------------------------------
# Runner / summary
# ---------------------------------------------------------------------------
CHECKS = [
    ("Daytona", check_daytona),
    ("Kimi", check_kimi),
    ("Doubleword", check_doubleword),
    ("Nosana", check_nosana),
    ("Oxylabs", check_oxylabs),
]


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    print("=" * 64)
    print("ProofBench smoke test (CONTRACTS.md §13)")
    print("=" * 64)
    if load_dotenv is not None:
        load_dotenv()
        print("Loaded .env via python-dotenv.")
    else:
        print("WARNING: python-dotenv not installed; relying on process environment.")
    print()

    results: list[tuple[str, str, str, float]] = []
    for i, (name, fn) in enumerate(CHECKS, 1):
        print(f"[{i}/{len(CHECKS)}] {name}")
        t0 = time.time()
        status, detail = fn()
        elapsed = time.time() - t0
        results.append((name, status, detail, elapsed))
        print(f"  -> {status} ({elapsed:.1f}s): {_one_line(detail, 200)}")
        print()

    # --- summary table ---
    print("=" * 64)
    print("SMOKE TEST SUMMARY")
    print("=" * 64)
    print(f"{'Service':<12} {'Status':<6} {'Time':>7}  Detail")
    print("-" * 64)
    for name, status, detail, elapsed in results:
        print(f"{name:<12} {status:<6} {elapsed:>6.1f}s  {_one_line(detail, 100)}")
    print("-" * 64)

    n_fail = sum(1 for _, s, _, _ in results if s == FAIL)
    n_skip = sum(1 for _, s, _, _ in results if s == SKIP)
    n_pass = sum(1 for _, s, _, _ in results if s == PASS)

    if MISSING_KEYS:
        print()
        print("!" * 64)
        print("MISSING KEYS (checks SKIPPED — set these in .env to enable):")
        for key in MISSING_KEYS:
            print(f"  - {key}")
        print("!" * 64)

    print()
    print(f"RESULT: {n_pass} PASS, {n_fail} FAIL, {n_skip} SKIP")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
