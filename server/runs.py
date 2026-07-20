"""Durable session registry and replayable SSE event queues."""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNS_DIR = os.path.join(ROOT, "runs")
_SESSIONS: dict[str, dict] = {}
_LOCK = threading.RLock()
_RUNTIME_KEYS = {"cond", "cancel_event"}


def _session_path(session_id: str) -> str:
    return os.path.join(RUNS_DIR, session_id, "session.json")


def _serializable(session: dict) -> dict:
    return {key: value for key, value in session.items() if key not in _RUNTIME_KEYS}


def persist_session(session: dict) -> None:
    """Atomically persist everything a refreshed browser needs to reconstruct."""
    os.makedirs(os.path.dirname(_session_path(session["id"])), exist_ok=True)
    target = _session_path(session["id"])
    temporary = f"{target}.tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(_serializable(session), handle, indent=2)
    os.replace(temporary, target)


def load_persisted_sessions() -> None:
    """Restore completed/history sessions after a backend restart.

    A live worker cannot safely survive a process restart, but its complete
    transcript and artifacts remain available. Browser refreshes keep using the
    already-running server worker.
    """
    if not os.path.isdir(RUNS_DIR):
        return
    for name in os.listdir(RUNS_DIR):
        path = _session_path(name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                session = json.load(handle)
            if not isinstance(session, dict) or not session.get("id"):
                continue
            session["cond"] = threading.Condition()
            session["cancel_event"] = threading.Event()
            # Threads are process-local. Do not falsely advertise a resumed job.
            session["is_running"] = False
            session.setdefault("events", [])
            session.setdefault("messages", [])
            session.setdefault("mode", "demo")
            _SESSIONS[session["id"]] = session
        except (OSError, json.JSONDecodeError):
            continue


def new_session(title: str = "New benchmark") -> dict:
    sid = uuid.uuid4().hex[:12]
    session = {
        "id": sid, "title": title, "phase": "INTAKE", "created_at": time.time(),
        "spec": None, "results": None, "mode": "demo", "messages": [], "events": [],
        "is_running": False, "dataset_path": None,
        "cond": threading.Condition(), "cancel_event": threading.Event(),
    }
    with _LOCK:
        _SESSIONS[sid] = session
        persist_session(session)
    return session


def get_session(session_id: str) -> dict | None:
    return _SESSIONS.get(session_id)


def delete_session(session_id: str) -> bool:
    """Remove an idle session and every persisted artifact associated with it."""
    with _LOCK:
        session = _SESSIONS.get(session_id)
        if session is None:
            return False
        if session.get("is_running"):
            raise RuntimeError("session is running")
        del _SESSIONS[session_id]

    run_dir = os.path.abspath(os.path.join(RUNS_DIR, session_id))
    runs_root = os.path.abspath(RUNS_DIR)
    if os.path.commonpath((runs_root, run_dir)) != runs_root:
        raise RuntimeError("invalid session path")
    if os.path.isdir(run_dir):
        shutil.rmtree(run_dir)
    return True


def list_sessions() -> list[dict]:
    return [{"id": s["id"], "title": s["title"], "phase": s["phase"],
             "mode": s.get("mode", "demo"), "is_running": s.get("is_running", False),
             "created_at": s["created_at"]}
            for s in sorted(_SESSIONS.values(), key=lambda s: s["created_at"])]


def public_session(session_id: str) -> dict | None:
    session = _SESSIONS.get(session_id)
    return _serializable(session) if session else None


def emit(session_id: str, event: str, data: dict) -> None:
    # Hold the registry lock through persistence. Without it, a chat worker
    # that had already obtained a session reference could recreate session.json
    # after another request deleted the session directory.
    with _LOCK:
        session = _SESSIONS.get(session_id)
        if not session:
            return
        if event == "state" and data.get("phase"):
            session["phase"] = data["phase"]
        if event == "artifact":
            if data.get("kind") == "spec":
                session["spec"] = data.get("spec")
            elif data.get("kind") == "results":
                session["results"] = data.get("metrics")
        with session["cond"]:
            session["events"].append((event, data))
            persist_session(session)
            session["cond"].notify_all()


def set_mode(session_id: str, mode: str) -> str:
    selected = "real" if str(mode).lower() == "real" else "demo"
    session = _SESSIONS.get(session_id)
    if session:
        session["mode"] = selected
        persist_session(session)
    return selected


def set_value(session_id: str, key: str, value) -> None:
    session = _SESSIONS.get(session_id)
    if session:
        session[key] = value
        persist_session(session)


def add_message(session_id: str, role: str, text: str) -> None:
    session = _SESSIONS.get(session_id)
    if session is None or not text:
        return
    session["messages"].append({"role": role, "text": str(text), "created_at": time.time()})
    persist_session(session)


def begin_run(session_id: str) -> bool:
    session = _SESSIONS.get(session_id)
    if not session or session.get("is_running"):
        return False
    session["cancel_event"].clear()
    session["is_running"] = True
    persist_session(session)
    return True


def request_stop(session_id: str) -> bool:
    session = _SESSIONS.get(session_id)
    if not session or not session.get("is_running"):
        return False
    session["cancel_event"].set()
    emit(session_id, "state", {"phase": "STOPPING", "candidates": {}})
    return True


def is_cancelled(session_id: str) -> bool:
    session = _SESSIONS.get(session_id)
    return bool(session and session["cancel_event"].is_set())


def finish_run(session_id: str, cancelled: bool = False) -> None:
    session = _SESSIONS.get(session_id)
    if not session:
        return
    session["is_running"] = False
    if cancelled:
        emit(session_id, "state", {"phase": "STOPPED", "candidates": {}})
    persist_session(session)


def persist_run(run_id: str, spec=None, metrics=None, report_md: str | None = None, citations=None) -> str:
    run_dir = os.path.join(RUNS_DIR, run_id)
    os.makedirs(run_dir, exist_ok=True)
    for filename, value in (("spec.json", spec), ("metrics.json", metrics), ("citations.json", citations)):
        if value is not None:
            with open(os.path.join(run_dir, filename), "w", encoding="utf-8") as handle:
                json.dump(value, handle, indent=2)
    if report_md is not None:
        with open(os.path.join(run_dir, "report.md"), "w", encoding="utf-8") as handle:
            handle.write(report_md)
    return run_dir


def load_run_results(run_id: str) -> dict | None:
    run_dir = os.path.join(RUNS_DIR, run_id)
    if not os.path.isdir(run_dir):
        return None
    def read_json(filename, default):
        path = os.path.join(run_dir, filename)
        return json.load(open(path, encoding="utf-8")) if os.path.exists(path) else default
    report_path = os.path.join(run_dir, "report.md")
    report = open(report_path, encoding="utf-8").read() if os.path.exists(report_path) else ""
    return {"metrics": read_json("metrics.json", None), "report_md": report,
            "citations": read_json("citations.json", [])}


load_persisted_sessions()
