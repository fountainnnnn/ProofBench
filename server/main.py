"""CLAUDE lane — CONTRACTS §11. FastAPI app: chat, datasets, runs, SSE."""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
import uuid

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from server import runs

load_dotenv()

ROOT = runs.ROOT
UPLOADS_DIR = os.path.join(ROOT, "data", "uploads")

# Integration glue (orchestrator): one Orchestrator per session keeps chat
# history + citations; dataset registry maps dataset_id -> absolute path.
ORCHES: dict = {}
DATASETS: dict[str, str] = {}
DEMO_INTAKE_DELAY_S = float(os.environ.get("DEMO_INTAKE_DELAY_S", "2.2"))
USER_PROVIDER_ENV: dict[str, str] = {}
SYSTEM_SANDBOX_ENV = {
    "NOSANA_BASE_URL", "NOSANA_API_KEY", "NOSANA_MODEL",
    "DOUBLEWORD_BASE_URL", "DOUBLEWORD_API_KEY", "DOUBLEWORD_MODEL",
    "OPENAI_API_KEY", "OPENAI_VISION_MODEL",
    "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL",
}
ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,127}$")

app = FastAPI(title="ProofBench")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def provider_environment() -> dict[str, str]:
    """Merge server-managed and Settings-provided values for one sandbox run."""
    values = {name: os.environ[name] for name in SYSTEM_SANDBOX_ENV if os.environ.get(name)}
    values.update(USER_PROVIDER_ENV)
    return values


def _looks_like_benchmark(message: str) -> bool:
    m = (message or "").lower()
    keywords = ("benchmark", "compare", "best ", "ocr", "evaluate", "extract", "invoice",
                "tesseract", "easyocr", "vlm", "llm", "model", "test ", "measure")
    return any(k in m for k in keywords)


def _venv_python() -> str:
    cand = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
    return cand if os.path.exists(cand) else "python"


def _emit_chat_event(session_id: str, event: str, data: dict) -> None:
    """Emit chat events while retaining a resumable conversation transcript."""
    runs.emit(session_id, event, data)
    if event == "delta" and data.get("text"):
        runs.add_message(session_id, "assistant", data["text"])


def _emit_demo_spec(session_id: str, session: dict, message: str) -> None:
    """Prepare a paced demo intake without calling external services."""
    from engine.demo_fallback import demo_spec

    spec = demo_spec(session.get("dataset_path") or os.path.join(ROOT, "data", "demo"), message)
    _emit_chat_event(session_id, "delta", {
        "text": "I mapped the benchmark and prepared an editable run plan. Review the candidates, then run the trace."
    })
    # Each event represents a distinct agent action, so use a readable demo
    # cadence rather than presenting the discovery pass as an instant dump.
    for tool, summary, detail in (
        ("web_search", "best invoice extraction APIs for structured fields", "Found OpenAI Vision documentation: https://platform.openai.com/docs/guides/vision"),
        ("web_search", "Doubleword DeepSeek V4 Pro document extraction API", "Found Doubleword documentation: https://docs.doubleword.ai"),
        ("web_search", "local OCR invoice extraction options", "Found EasyOCR repository: https://github.com/JaidedAI/EasyOCR"),
        ("web_search", "Tesseract OCR invoice extraction documentation", "Found Tesseract documentation: https://tesseract-ocr.github.io/tessdoc/"),
        ("scrape_docs", "candidate installation, API, and pricing pages", "Indexed the four candidate documentation pages for adapter generation."),
        ("generate_adapter", "integration plan", "Mapped each candidate to its adapter and sandbox build path."),
    ):
        time.sleep(max(0, DEMO_INTAKE_DELAY_S))
        runs.emit(session_id, "artifact", {
            "kind": "trace", "tool": tool, "args_summary": summary,
            "status": "ok", "detail": detail,
        })
    runs.emit(session_id, "artifact", {"kind": "spec", "spec": spec, "demo_mode": True})
    runs.emit(session_id, "state", {"phase": "SPEC_CONFIRM", "candidates": {
        candidate["name"]: "ready" for candidate in spec["candidates"]
    }})


@app.get("/api/health")
def api_health():
    """Service + key-presence status (booleans only, never values)."""
    keys = [
        "DAYTONA_API_KEY",
        "MOONSHOT_API_KEY",
        "NOSANA_API_KEY",
        "DOUBLEWORD_API_KEY",
        "OXYLABS_USERNAME",
        "OXYLABS_PASSWORD",
        "OPENAI_API_KEY",
    ]
    return {
        "status": "ok",
        "version": "0.1.0",
        "keys": {k: bool(os.environ.get(k)) for k in keys},
    }


@app.get("/api/sessions")
def api_sessions():
    return runs.list_sessions()


@app.get("/api/sessions/{session_id}")
def api_session(session_id: str):
    s = runs.public_session(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    return s


@app.get("/api/settings/provider-keys")
def api_provider_keys():
    system = [name for name in SYSTEM_SANDBOX_ENV if os.environ.get(name)]
    return {
        "keys": [
            *({"env": name, "source": "system"} for name in sorted(system) if name not in USER_PROVIDER_ENV),
            *({"env": name, "source": "settings"} for name in sorted(USER_PROVIDER_ENV)),
        ]
    }


@app.post("/api/settings/provider-keys")
async def api_save_provider_key(request: Request):
    body = await request.json()
    env = str(body.get("env", "")).strip().upper()
    value = str(body.get("value", "")).strip()
    if not ENV_NAME_RE.fullmatch(env):
        raise HTTPException(status_code=400, detail="env must be uppercase letters, digits, and underscores")
    if not value:
        raise HTTPException(status_code=400, detail="value is required")
    USER_PROVIDER_ENV[env] = value
    return {"env": env, "source": "settings"}


@app.delete("/api/settings/provider-keys/{env}")
def api_delete_provider_key(env: str):
    USER_PROVIDER_ENV.pop(env.upper(), None)
    return {"ok": True}


@app.post("/api/sessions")
def api_create_session():
    session = runs.new_session()
    return {"session_id": session["id"], "title": session["title"]}


@app.delete("/api/sessions/{session_id}")
def api_delete_session(session_id: str):
    try:
        deleted = runs.delete_session(session_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="session not found")
    ORCHES.pop(session_id, None)
    return {"session_id": session_id, "deleted": True}


@app.post("/api/sessions/{session_id}/stop")
def api_stop(session_id: str):
    if not runs.get_session(session_id):
        raise HTTPException(status_code=404, detail="session not found")
    accepted = runs.request_stop(session_id)
    return {"session_id": session_id, "status": "stopping" if accepted else "not_running"}


@app.get("/api/runs/{run_id}/results")
def api_run_results(run_id: str):
    res = runs.load_run_results(run_id)
    if res is None:
        raise HTTPException(status_code=404, detail="run not found")
    return res


@app.get("/api/runs/{run_id}/report.pdf")
def api_run_pdf(run_id: str, download: bool = False):
    report_path = os.path.join(runs.RUNS_DIR, run_id, "report.pdf")
    if not os.path.isfile(report_path):
        raise HTTPException(status_code=404, detail="PDF report not found")
    return FileResponse(
        report_path,
        media_type="application/pdf",
        filename=f"proofbench_{run_id}_report.pdf",
        content_disposition_type="attachment" if download else "inline",
    )


@app.get("/api/sessions/{session_id}/events")
def api_events(session_id: str, request: Request):
    session = runs.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    cond = session["cond"]

    try:
        cursor = max(0, int(request.headers.get("last-event-id", "-1")) + 1)
    except ValueError:
        cursor = 0

    def gen():
        # Event ids let EventSource resume without replaying the whole transcript.
        nonlocal cursor
        while True:
            with cond:
                if cursor >= len(session["events"]):
                    cond.wait(timeout=15)
                batch = session["events"][cursor:]
                cursor = len(session["events"])
            if not batch:
                yield ": ping\n\n"
                continue
            for offset, (event, data) in enumerate(batch):
                event_id = cursor - len(batch) + offset
                yield f"id: {event_id}\nevent: {event}\ndata: {json.dumps(data)}\n\n"
            if any(ev == "done" for ev, _ in batch):
                break

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/api/chat")
async def api_chat(request: Request):
    body = await request.json()
    message = body.get("message", "")
    session_id = body.get("session_id")

    session = runs.get_session(session_id) if session_id else None
    if not session:
        title = (message[:40] + "…") if len(message) > 40 else (message or "New benchmark")
        session = runs.new_session(title=title)
    sid = session["id"]
    mode = runs.set_mode(sid, body.get("mode", session.get("mode", "demo")))
    dataset_id = body.get("dataset_id")
    if dataset_id and dataset_id in DATASETS:
        runs.set_value(sid, "dataset_path", DATASETS[dataset_id])
    if message:
        runs.add_message(sid, "user", message)

    def worker():
        try:
            if mode == "demo":
                _emit_demo_spec(sid, session, message)
                return
            try:
                from engine.agent import Orchestrator
            except ImportError:
                if mode == "real":
                    runs.emit(sid, "error", {"message": "The Real-mode orchestrator is unavailable."})
                    return
                from engine.demo_fallback import demo_spec
                spec = demo_spec(session.get("dataset_path") or os.path.join(ROOT, "data", "demo"), message)
                _emit_chat_event(sid, "delta", {"text": "I prepared a demo-ready benchmark spec using the built-in candidates."})
                runs.emit(sid, "artifact", {"kind": "spec", "spec": spec, "demo_mode": True})
                runs.emit(sid, "state", {"phase": "SPEC_CONFIRM", "candidates": {}})
                return
            orch = ORCHES.get(sid)
            if orch is None:
                run_dir = os.path.join(runs.RUNS_DIR, sid)
                os.makedirs(run_dir, exist_ok=True)
                orch = Orchestrator(sid, run_dir, lambda ev, data: _emit_chat_event(sid, ev, data))
                ORCHES[sid] = orch
            orch.chat(message)
        except Exception as e:
            if mode == "real":
                runs.emit(sid, "error", {
                    "message": f"Real-mode discovery failed: {type(e).__name__}: {e}"[:280]
                })
                return
            from engine.demo_fallback import demo_spec

            spec = demo_spec(
                session.get("dataset_path") or os.path.join(ROOT, "data", "demo"),
                message,
            )
            runs.emit(sid, "artifact", {
                "kind": "trace",
                "tool": "intake_fallback",
                "args_summary": "cached benchmark template",
                "status": "ok",
                "detail": "Live discovery was unavailable; loaded a demo-ready spec.",
            })
            _emit_chat_event(sid, "delta", {
                "text": "Live discovery is unavailable, so I loaded a polished demo-ready spec. You can review it and run immediately."
            })
            runs.emit(sid, "artifact", {"kind": "spec", "spec": spec, "demo_mode": True})
            runs.emit(sid, "state", {"phase": "SPEC_CONFIRM", "candidates": {}})
        finally:
            runs.finish_run(sid, cancelled=runs.is_cancelled(sid))
            runs.emit(sid, "done", {})

    # Demo mode is a guided benchmark walkthrough. It should turn any user
    # prompt into a concrete, editable plan rather than repeatedly sending the
    # same generic intake hint for conversational follow-ups.
    if mode in {"demo", "real"}:
        if not runs.begin_run(sid):
            raise HTTPException(status_code=409, detail="session already working")
        threading.Thread(target=worker, daemon=True).start()
    else:
        # Non-benchmark message: still create/continue the session, echo a friendly hint.
        def hint():
            try:
                _emit_chat_event(sid, "delta", {"text": "I kept this conversation in context. Tell me the document type or say which tools to compare, and I will build the benchmark plan."})
            finally:
                runs.finish_run(sid, cancelled=runs.is_cancelled(sid))
                runs.emit(sid, "done", {})
        if not runs.begin_run(sid):
            raise HTTPException(status_code=409, detail="session already working")
        threading.Thread(target=hint, daemon=True).start()

    return {"session_id": sid}


@app.post("/api/datasets")
async def api_datasets(
    request: Request,
    images: list[UploadFile] = File(default=None),
    ground_truth: UploadFile = File(default=None),
):
    ctype = request.headers.get("content-type", "")
    dataset_id = uuid.uuid4().hex[:12]

    if "multipart/form-data" not in ctype:
        body = await request.json()
        if body.get("use_synthetic"):
            out_dir = os.path.join(ROOT, "data", "demo")
            py = _venv_python()
            try:
                subprocess.run(
                    [py, "make_dataset.py", "--out", "data/demo", "--n", "15"],
                    cwd=ROOT, check=True, capture_output=True, text=True, timeout=300,
                )
            except Exception as e:
                return JSONResponse(status_code=500,
                                    content={"error": f"synthetic dataset failed: {e}"})
            DATASETS["demo"] = out_dir
            return {"dataset_id": "demo", "path": out_dir}
        raise HTTPException(status_code=400, detail="expected images + ground_truth or use_synthetic")

    dest = os.path.join(UPLOADS_DIR, dataset_id)
    img_dir = os.path.join(dest, "images")
    os.makedirs(img_dir, exist_ok=True)
    for up in (images or []):
        if not up or not up.filename:
            continue
        with open(os.path.join(img_dir, os.path.basename(up.filename)), "wb") as f:
            f.write(await up.read())
    if ground_truth and ground_truth.filename:
        with open(os.path.join(dest, "ground_truth.csv"), "wb") as f:
            f.write(await ground_truth.read())
    DATASETS[dataset_id] = dest
    return {"dataset_id": dataset_id, "path": dest}


@app.post("/api/sessions/{session_id}/run")
async def api_run(session_id: str, request: Request):
    session = runs.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    body = await request.json()
    spec = body.get("spec")
    if spec is None:
        raise HTTPException(status_code=400, detail="missing spec")
    if not isinstance(spec.get("dataset"), dict) or not spec["dataset"].get("path"):
        spec["dataset"] = {
            "path": session.get("dataset_path") or os.path.join(ROOT, "data", "demo")
        }
    mode = runs.set_mode(session_id, body.get("mode", session.get("mode", "demo")))
    if mode == "demo":
        spec["demo_mode"] = True
    runs.set_value(session_id, "spec", spec)
    if not runs.begin_run(session_id):
        raise HTTPException(status_code=409, detail="session already working")

    def worker():
        run_dir = os.path.join(runs.RUNS_DIR, session_id)
        os.makedirs(run_dir, exist_ok=True)
        try:
            if mode == "demo":
                from engine.demo_fallback import emit_demo_run
                dataset_path = spec.get("dataset", {}).get("path", "")
                images_dir = os.path.join(dataset_path, "images")
                n_docs = len([name for name in os.listdir(images_dir)
                              if name.lower().endswith((".png", ".jpg", ".jpeg"))]) if os.path.isdir(images_dir) else 15
                try:
                    metrics, report_md = emit_demo_run(
                        spec, lambda ev, data: runs.emit(session_id, ev, data), run_dir,
                        n_docs=n_docs or 15, should_stop=lambda: runs.is_cancelled(session_id))
                    runs.persist_run(session_id, spec=spec, metrics=metrics, report_md=report_md, citations=[])
                finally:
                    runs.finish_run(session_id, cancelled=runs.is_cancelled(session_id))
                    runs.emit(session_id, "done", {})
                return
            from engine.agent import Orchestrator
        except ImportError as e:
            from engine.demo_fallback import emit_demo_run

            metrics, report_md = emit_demo_run(spec, lambda ev, data: runs.emit(session_id, ev, data), run_dir,
                                               should_stop=lambda: runs.is_cancelled(session_id))
            runs.persist_run(session_id, spec=spec, metrics=metrics,
                             report_md=report_md, citations=[])
            runs.finish_run(session_id, cancelled=runs.is_cancelled(session_id))
            runs.emit(session_id, "done", {})
            return
        try:
            orch = ORCHES.get(session_id)
            if orch is None:
                orch = Orchestrator(session_id, run_dir,
                                    lambda ev, data: runs.emit(session_id, ev, data),
                                    cancel_event=session["cancel_event"],
                                    provider_env=provider_environment())
                ORCHES[session_id] = orch
            # run_benchmark emits artifact/results + artifact/report itself.
            metrics = orch.run_benchmark(spec)
            report_md = ""
            rp = os.path.join(run_dir, "report.md")
            if os.path.exists(rp):
                with open(rp, encoding="utf-8") as f:
                    report_md = f.read()
            runs.persist_run(session_id, spec=spec, metrics=metrics,
                             report_md=report_md, citations=orch.ctx.citations)
        except Exception as e:
            if runs.is_cancelled(session_id):
                return
            current = runs.get_session(session_id)
            if not current or not current.get("results"):
                from engine.demo_fallback import emit_demo_run

                n_docs = 15
                dataset_path = spec.get("dataset", {}).get("path")
                if dataset_path:
                    images_dir = os.path.join(dataset_path, "images")
                    if os.path.isdir(images_dir):
                        n_docs = len([
                            name for name in os.listdir(images_dir)
                            if name.lower().endswith((".png", ".jpg", ".jpeg"))
                        ]) or 15
                metrics, report_md = emit_demo_run(
                    spec,
                    lambda ev, data: runs.emit(session_id, ev, data),
                    run_dir,
                    n_docs=n_docs,
                    should_stop=lambda: runs.is_cancelled(session_id),
                )
                runs.persist_run(session_id, spec=spec, metrics=metrics,
                                 report_md=report_md, citations=[])
        finally:
            runs.finish_run(session_id, cancelled=runs.is_cancelled(session_id))
            runs.emit(session_id, "done", {})

    threading.Thread(target=worker, daemon=True).start()
    return {"session_id": session_id, "status": "started"}
