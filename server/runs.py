"""Durable session/run facade backed by SQLite and confined artifact directories."""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
from contextlib import contextmanager

from server.security import redact_event_data
from server.state import BusyError, QuotaError, SQLiteStore

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNS_DIR = os.path.join(ROOT, "runs")
MAX_EVENTS = int(os.environ.get("PROOFBENCH_MAX_EVENTS", "1000"))
MAX_MESSAGES = int(os.environ.get("PROOFBENCH_MAX_MESSAGES", "200"))
MAX_MESSAGE_CHARS = int(os.environ.get("PROOFBENCH_MAX_MESSAGE_CHARS", "10000"))
MAX_EVENT_CHARS = int(os.environ.get("PROOFBENCH_MAX_EVENT_CHARS", "16384"))
MAX_EVENT_BYTES = int(os.environ.get("PROOFBENCH_MAX_EVENT_BYTES", str(2 * 1024 * 1024)))
MAX_CONCURRENT_RUNS = int(os.environ.get("PROOFBENCH_MAX_CONCURRENT_RUNS_PER_TENANT", "2"))
RUNS_PER_DAY = int(os.environ.get("PROOFBENCH_RUNS_PER_DAY", "100"))
MAX_CONCURRENT_CHATS = int(os.environ.get("PROOFBENCH_MAX_CONCURRENT_CHATS_PER_TENANT", "4"))
CHATS_PER_DAY = int(os.environ.get("PROOFBENCH_CHATS_PER_DAY", "500"))
REQUESTS_PER_MINUTE = int(os.environ.get("PROOFBENCH_REQUESTS_PER_MINUTE", "600"))
ID_RE = re.compile(r"^[a-f0-9]{12}$")
WINDOWS_PATH_RE = re.compile(r"(?:[A-Za-z]:\\|\\\\)[^\r\n`\"']+")
POSIX_PATH_RE = re.compile(r"(^|[\s(`\"'=,])/(?!/)[^\s`\"'():),]+", re.MULTILINE)
LOGGER = logging.getLogger("proofbench.state")
STORE = SQLiteStore(os.environ.get("PROOFBENCH_STATE_DB", os.path.join(RUNS_DIR, "proofbench.sqlite3")),
                    max_events=MAX_EVENTS, max_event_bytes=MAX_EVENT_BYTES,
                    max_messages=MAX_MESSAGES, recover_interrupted=True,
                    lease_seconds=int(os.environ.get("PROOFBENCH_JOB_LEASE_SECONDS", "90")),
                    orphan_grace_seconds=int(os.environ.get("PROOFBENCH_ORPHAN_GRACE_SECONDS", "120")),
                    artifact_root=RUNS_DIR)


class PersistentCancelEvent:
    def __init__(self, session_id: str):
        self.session_id = session_id

    def is_set(self) -> bool:
        return STORE.is_cancelled(self.session_id)

    def set(self) -> None:
        STORE.request_stop(self.session_id)

    def clear(self) -> None:
        return None


def configure_store(store: SQLiteStore, runs_dir: str | None = None) -> None:
    global STORE, RUNS_DIR
    STORE = store
    if runs_dir is not None:
        RUNS_DIR = os.path.abspath(runs_dir)
        STORE.artifact_root = RUNS_DIR


def _valid_id(value: str) -> bool:
    return bool(ID_RE.fullmatch(str(value)))


def _confined_dir(root: str, item_id: str) -> str:
    if not _valid_id(item_id):
        raise ValueError("invalid id")
    root = os.path.realpath(root)
    target = os.path.realpath(os.path.join(root, item_id))
    if os.path.commonpath((root, target)) != root:
        raise ValueError("path escapes storage root")
    return target


def _run_dir(run_id: str) -> str:
    return _confined_dir(RUNS_DIR, run_id)


def _session_state_dir(session_id: str) -> str:
    return _confined_dir(os.path.join(RUNS_DIR, "sessions"), session_id)


def _confined_artifact(directory: str, filename: str) -> str:
    path = os.path.realpath(os.path.join(directory, filename))
    if os.path.commonpath((os.path.realpath(directory), path)) != os.path.realpath(directory):
        raise ValueError("artifact path escapes its directory")
    return path


def _public_data(value, key: str = ""):
    if key.lower() in {"path", "dataset_path", "run_dir", "local_path"}:
        return "[server path]"
    if isinstance(value, dict):
        return {str(child_key): _public_data(child, str(child_key))
                for child_key, child in value.items()}
    if isinstance(value, list):
        return [_public_data(child) for child in value]
    if isinstance(value, tuple):
        return tuple(_public_data(child) for child in value)
    if isinstance(value, str):
        return POSIX_PATH_RE.sub(r"\1[server path]", WINDOWS_PATH_RE.sub("[server path]", value))
    return value


def session_dir(session_id: str, owner: str) -> str:
    if not STORE.get_session(session_id, owner):
        raise KeyError(session_id)
    path = _session_state_dir(session_id)
    os.makedirs(path, exist_ok=True)
    return path


def run_dir(run_id: str, owner: str) -> str:
    if not STORE.get_run(run_id, owner):
        raise KeyError(run_id)
    path = _run_dir(run_id)
    os.makedirs(path, exist_ok=True)
    return path


def new_session(owner: str, title: str = "New benchmark", dataset_id: str | None = None) -> dict:
    title = redact_event_data({"title": str(title)}, owner)["title"][:160]
    session = STORE.create_session(owner, title, dataset_id)
    session["cancel_event"] = PersistentCancelEvent(session["id"])
    return session


def _runtime_session(session: dict | None) -> dict | None:
    if session:
        session["cancel_event"] = PersistentCancelEvent(session["id"])
    return session


def get_session(session_id: str, owner: str | None = None) -> dict | None:
    if not _valid_id(session_id):
        return None
    return _runtime_session(STORE.get_session(session_id, owner))


def public_session(session_id: str, owner: str) -> dict | None:
    session = get_session(session_id, owner)
    if not session:
        return None
    session.pop("owner", None)
    session.pop("cancel_event", None)
    session.pop("dataset_path", None)
    session.pop("active_worker_id", None)
    session.pop("active_lease_expires_at", None)
    session.pop("active_job_id", None)
    session.pop("active_kind", None)
    session.pop("latest_job_id", None)
    spec = session.get("spec")
    if isinstance(spec, dict) and isinstance(spec.get("dataset"), dict):
        spec = dict(spec)
        spec["dataset"] = ({"dataset_id": session.get("dataset_id")}
                           if session.get("dataset_id") else {})
        session["spec"] = spec
    session["events"] = [(sequence, event, _public_data(data))
                         for sequence, event, data in session.get("events", [])]
    return session


def _summary_provenance(session: dict) -> str:
    """Evidence status for a session row, using the same rule as load_run_results.

    A session's `mode` is never consulted: it defaults to 'real' and would let a
    session that has produced nothing render as measured evidence.
    """
    if not session.get("latest_run_id") or not session.get("latest_run_has_metrics"):
        return "pending"
    provenance = session.get("latest_run_provenance")
    if provenance not in {"measured", "synthetic"}:
        # Legacy/corrupt rows must not silently become measured evidence.
        return "unverified"
    return provenance


def list_sessions(owner: str) -> list[dict]:
    sessions = STORE.list_sessions(owner)
    for session in sessions:
        session["provenance"] = _summary_provenance(session)
        # Terminal failure is a display state distinct from "no evidence yet".
        session["latest_run_failed"] = session.get("latest_run_status") == "failed"
        session.pop("latest_run_provenance", None)
        session.pop("latest_run_has_metrics", None)
    return sessions


def candidate_docs_urls(owner: str) -> dict[str, str]:
    """Every candidate this tenant has benchmarked, mapped to its docs URL.

    The logo endpoint resolves marks only for names in here, from only these
    URLs, so it can never be pointed at a host of the caller's choosing. Each
    URL was validated as a public HTTP(S) address at the schema boundary and has
    already been fetched by the run itself.
    """
    urls: dict[str, str] = {}
    for summary in STORE.list_sessions(owner):
        session = STORE.get_session(summary["id"], owner) or {}
        spec = session.get("spec")
        if not isinstance(spec, dict):
            continue
        for candidate in spec.get("candidates") or []:
            name = str((candidate or {}).get("name") or "")
            if name and name not in urls:
                urls[name] = str(candidate.get("docs_url") or "")
    return urls


def delete_session(session_id: str, owner: str) -> bool:
    try:
        deleted, run_ids = STORE.delete_session(session_id, owner)
    except BusyError as exc:
        raise RuntimeError(str(exc)) from exc
    if not deleted:
        return False
    for path in [_session_state_dir(session_id), *(_run_dir(run_id) for run_id in run_ids)]:
        STORE.enqueue_deletion("session" if path == _session_state_dir(session_id) else "run",
                               session_id if path == _session_state_dir(session_id) else os.path.basename(path),
                               path)
    process_deletion_queue([RUNS_DIR])
    return True


def _bounded_data(event: str, data: dict) -> dict:
    if not isinstance(data, dict):
        return {"message": "invalid event payload"}
    value = dict(data)
    if event == "artifact" and value.get("kind") == "sandbox_log":
        value["line"] = str(value.get("line", "")).replace("\r", " ").replace("\n", " ")[:300]
    if event == "artifact" and value.get("kind") == "sandbox_file":
        value["sandbox"] = str(value.get("sandbox", ""))[:160]
        value["path"] = str(value.get("path", ""))[:160]
        value["language"] = str(value.get("language", ""))[:40]
        value["content"] = str(value.get("content", ""))[:12_200]
    encoded = json.dumps(value, default=str)
    if len(encoded) > MAX_EVENT_CHARS:
        if event == "delta":
            return {"text": str(value.get("text", ""))[:MAX_EVENT_CHARS // 2]}
        return {"message": "event payload exceeded persistence limit"}
    return value


def emit(session_id: str, event: str, data: dict, run_id: str | None = None) -> None:
    owner = STORE.session_owner(session_id)
    if not owner:
        return
    cleaned = _bounded_data(event, redact_event_data(data, owner))
    size = len(json.dumps((event, cleaned), default=str).encode("utf-8"))
    STORE.append_event(session_id, event, cleaned, size, run_id)


def events_since(session_id: str, owner: str, cursor: int):
    return STORE.events_since(session_id, owner, cursor)


def event_records_since(session_id: str, owner: str, cursor: int):
    rows = STORE.event_records_since(session_id, owner, cursor)
    return ([(sequence, event, _public_data(data), job_id)
             for sequence, event, data, job_id in rows] if rows is not None else None)


def stream_state(session_id: str, owner: str):
    return STORE.stream_state(session_id, owner)


def set_value(session_id: str, key: str, value) -> None:
    owner = STORE.session_owner(session_id)
    if owner:
        cleaned = redact_event_data({key: value}, owner)[key]
        STORE.set_value(session_id, key, cleaned)


def add_message(session_id: str, role: str, text: str) -> None:
    owner = STORE.session_owner(session_id)
    if not owner or not text:
        return
    clean = redact_event_data({"text": str(text)}, owner)["text"]
    STORE.add_message(session_id, "assistant" if role == "assistant" else "user",
                      clean[:MAX_MESSAGE_CHARS])


SCRAPER_ORDER_KEY = "scraper_order"


def scraper_order(owner: str) -> tuple[str, ...]:
    """The tenant's provider order, falling back to the measured default."""
    from engine import scrapers

    return scrapers.parse_order(STORE.get_setting(owner, SCRAPER_ORDER_KEY))


def set_scraper_order(owner: str, order) -> tuple[str, ...]:
    """Store a provider order, normalized so a bad value cannot disable scraping."""
    from engine import scrapers

    cleaned = scrapers.parse_order(order)
    STORE.set_setting(owner, SCRAPER_ORDER_KEY, " ".join(cleaned))
    return cleaned


DEFAULT_PROVIDER_KEY = "default_provider:"
PINNABLE_CAPABILITIES = ("orchestration", "assessment", "codegen")


def default_providers(owner: str) -> dict[str, str]:
    """The operator's chosen default provider per capability, where set."""
    chosen = {}
    for capability in PINNABLE_CAPABILITIES:
        value = STORE.get_setting(owner, DEFAULT_PROVIDER_KEY + capability)
        if value:
            chosen[capability] = value
    return chosen


def set_default_provider(owner: str, capability: str, provider: str | None) -> None:
    """Pin a capability to a provider, or clear the pin with a falsy value.

    An unconfigured provider is accepted on purpose: `capability_providers` only
    reorders by the pin and filters by what is configured, so a pin can never
    disable a capability, and an operator is allowed to choose the provider they
    are about to add a key for.
    """
    from engine.llm_clients import PROVIDERS

    if capability not in PINNABLE_CAPABILITIES:
        raise ValueError(f"unknown capability: {capability}")
    key = DEFAULT_PROVIDER_KEY + capability
    if not provider:
        STORE.set_setting(owner, key, "")
        return
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider: {provider}")
    STORE.set_setting(owner, key, provider)


def provider_keys(owner: str) -> dict[str, str]:
    """Every credential this tenant has stored, by env name."""
    return STORE.credentials(owner)


def set_provider_key(owner: str, env: str, value: str) -> None:
    STORE.set_credential(owner, env, value)


def delete_provider_key(owner: str, env: str) -> None:
    STORE.delete_credential(owner, env)


def add_findings(session_id: str, items) -> None:
    """Persist what the agent looked up, redacted the same way messages are.

    Only titles and URLs, so nothing here can carry a scraped page — but it is
    still model-influenced text bound for durable storage, and it goes through
    the same redaction every other such value does.
    """
    owner = STORE.session_owner(session_id)
    if not owner:
        return
    clean = []
    for item in items or []:
        url = str((item or {}).get("url") or "")
        title = str((item or {}).get("title") or "")
        if not url:
            continue
        safe = redact_event_data({"title": title, "url": url}, owner)
        clean.append({"title": safe["title"], "url": safe["url"]})
    if clean:
        STORE.add_findings(session_id, clean)


def list_findings(session_id: str) -> list[dict]:
    return STORE.list_findings(session_id)


def begin_run(session_id: str) -> bool:
    return STORE.claim_chat(session_id)


def begin_chat(session_id: str, owner: str, dataset_id: str | None, mode: str,
               message: str) -> dict:
    clean = redact_event_data({"text": str(message)}, owner)["text"][:MAX_MESSAGE_CHARS]
    return STORE.claim_chat_atomic(session_id, owner, dataset_id, mode, clean,
                                   MAX_CONCURRENT_CHATS, CHATS_PER_DAY)


def begin_benchmark(session_id: str, owner: str, spec: dict, mode: str,
                    dataset_id: str | None = None) -> dict:
    return STORE.claim_benchmark(session_id, owner, spec, mode,
                                 MAX_CONCURRENT_RUNS, RUNS_PER_DAY, dataset_id)


def request_stop(session_id: str) -> bool:
    accepted = STORE.request_stop(session_id)
    if accepted:
        session = STORE.get_session(session_id)
        emit(session_id, "state", {"phase": "STOPPING", "candidates": {}},
             session.get("active_job_id") if session else None)
    return accepted


def is_cancelled(session_id: str) -> bool:
    return STORE.is_cancelled(session_id)


def finish_run(session_id: str, cancelled: bool = False, failed: bool = False,
               emit_done: bool = False, run_id: str | None = None,
               job_id: str | None = None) -> None:
    event_job_id = run_id or job_id
    if cancelled:
        emit(session_id, "state", {"phase": "STOPPED", "candidates": {}}, event_job_id)
    elif failed:
        emit(session_id, "state", {"phase": "FAILED", "candidates": {}}, event_job_id)
    if emit_done:
        emit(session_id, "done", {}, event_job_id)
    STORE.finish(session_id, run_id=run_id, job_id=job_id,
                 cancelled=cancelled, failed=failed)


@contextmanager
def job_heartbeat(session_id: str, job_id: str):
    stopped = threading.Event()

    def pulse():
        interval = max(5, STORE.lease_seconds // 3)
        while not stopped.wait(interval):
            try:
                if not STORE.heartbeat_job(session_id, job_id):
                    return
                STORE.heartbeat_worker()
            except Exception as exc:
                LOGGER.warning(json.dumps({"event": "job_heartbeat_failed",
                                           "error_type": type(exc).__name__}))

    STORE.heartbeat_job(session_id, job_id)
    thread = threading.Thread(target=pulse, daemon=True, name=f"proofbench-job-{job_id[:8]}")
    thread.start()
    try:
        yield
    finally:
        stopped.set()


def persist_run(run_id: str, spec=None, metrics=None, report_md: str | None = None,
                citations=None, provenance: str = "measured") -> str:
    with STORE.connect() as connection:
        row = connection.execute("SELECT owner FROM benchmark_runs WHERE id=?", (run_id,)).fetchone()
    if not row:
        raise KeyError(run_id)
    owner = row["owner"]
    cleaned = redact_event_data({"spec": spec, "metrics": metrics,
                                 "report": report_md, "citations": citations}, owner)
    spec, metrics = cleaned["spec"], cleaned["metrics"]
    report_md, citations = cleaned["report"], cleaned["citations"]
    directory = run_dir(run_id, owner)
    metrics_document = {"provenance": provenance, "metrics": metrics} if metrics is not None else None
    existing_metrics = os.path.join(directory, "metrics.json")
    if os.path.isfile(existing_metrics):
        try:
            with open(existing_metrics, encoding="utf-8") as handle:
                existing = json.load(handle)
            if isinstance(existing, dict) and existing.get("provenance") in {"measured", "synthetic"} and isinstance(
                    existing.get("metrics"), dict):
                metrics_document = existing
                metrics = existing["metrics"]
                provenance = existing["provenance"]
        except (OSError, json.JSONDecodeError):
            pass
    for filename, value in (("spec.json", spec), ("metrics.json", metrics_document),
                            ("citations.json", citations)):
        if value is not None:
            target = os.path.join(directory, filename)
            temporary = target + ".tmp"
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump(value, handle, indent=2)
            os.replace(temporary, target)
    if report_md is not None:
        target = os.path.join(directory, "report.md")
        temporary = target + ".tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            handle.write(str(report_md)[:5_000_000])
        os.replace(temporary, target)
    STORE.update_run_artifacts(run_id, spec=spec, metrics=metrics,
                               report_md=report_md, citations=citations,
                               provenance=provenance)
    return directory


def resolve_run_id(value: str, owner: str) -> str | None:
    return value if STORE.get_run(value, owner) else None


def load_run_results(run_id: str, owner: str) -> dict | None:
    run = STORE.get_run(run_id, owner)
    if not run:
        return None
    directory = _run_dir(run_id)

    def read_json(filename, default):
        path = _confined_artifact(directory, filename)
        if not os.path.isfile(path):
            return default
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)

    report_path = _confined_artifact(directory, "report.md")
    report = run.get("report_md") or ""
    if os.path.isfile(report_path):
        with open(report_path, encoding="utf-8") as handle:
            report = handle.read()
    stored_metrics = run.get("metrics")
    provenance = run.get("provenance")
    if stored_metrics is None:
        provenance = "pending"
    elif provenance not in {"measured", "synthetic"}:
        # Legacy/corrupt rows must not silently become measured evidence.
        provenance = "unverified"
        stored_metrics = None
    return _public_data({"metrics": stored_metrics, "provenance": provenance, "report_md": report,
                         "citations": read_json("citations.json", run.get("citations", [])),
                         "run_id": run_id, "session_id": run["session_id"],
                         "status": run["status"]})


def cleanup_expired(retention_days: int) -> dict[str, list[str]]:
    removed = STORE.cleanup_expired(retention_days)
    for session_id in removed["session_ids"]:
        STORE.enqueue_deletion("session", session_id, _session_state_dir(session_id))
    for run_id in removed["run_ids"]:
        STORE.enqueue_deletion("run", run_id, _run_dir(run_id))
    process_deletion_queue([RUNS_DIR])
    return removed


def consume_request(owner: str) -> bool:
    return STORE.consume_request(owner, REQUESTS_PER_MINUTE)


def register_dataset(dataset_id: str, owner: str, path: str, *, kind: str = "upload",
                     image_count: int = 0, total_bytes: int = 0) -> None:
    STORE.register_dataset(dataset_id, owner, os.path.realpath(path), kind=kind,
                           image_count=image_count, total_bytes=total_bytes)


def get_dataset(dataset_id: str, owner: str) -> dict | None:
    return STORE.get_dataset(dataset_id, owner)


def reserve_dataset(owner: str, path_for_id, total_bytes: int, quota_bytes: int) -> dict:
    return STORE.reserve_dataset(owner, path_for_id, total_bytes, quota_bytes)


def activate_dataset(dataset_id: str, owner: str, image_count: int, total_bytes: int,
                     kind: str = "upload") -> dict:
    return STORE.activate_dataset(dataset_id, owner, image_count=image_count,
                                  total_bytes=total_bytes, kind=kind)


def release_dataset_reservation(dataset_id: str, owner: str) -> None:
    STORE.release_dataset_reservation(dataset_id, owner)


def release_stale_dataset_reservations() -> int:
    return STORE.release_stale_dataset_reservations()


def bind_dataset(session_id: str, owner: str, dataset_id: str, path: str) -> None:
    STORE.bind_dataset(session_id, owner, dataset_id, os.path.realpath(path))


def list_datasets(owner: str) -> list[dict]:
    return STORE.list_datasets(owner)


def begin_dataset_delete(dataset_id: str, owner: str) -> dict | None:
    return STORE.begin_dataset_delete(dataset_id, owner)


def finish_dataset_delete(dataset_id: str, success: bool) -> None:
    STORE.finish_dataset_delete(dataset_id, success)


def active_job_contract() -> dict:
    return STORE.active_job_contract()


def recover_stale_jobs() -> int:
    return STORE.recover_stale_jobs()


def acquire_leader(name: str, ttl_seconds: int = 90) -> bool:
    return STORE.acquire_leader(name, ttl_seconds)


def process_deletion_queue(allowed_roots: list[str]) -> dict:
    roots = [os.path.realpath(root) for root in allowed_roots]
    deleted = 0
    failed = 0
    for item in STORE.due_deletions():
        path = os.path.realpath(item["path"])
        try:
            if not any(os.path.commonpath((root, path)) == root for root in roots):
                raise ValueError("deletion target outside configured storage")
            if os.path.isdir(path):
                shutil.rmtree(path)
            elif os.path.exists(path):
                os.remove(path)
            STORE.finish_deletion(item["id"], True)
            deleted += 1
        except (OSError, ValueError) as exc:
            STORE.finish_deletion(item["id"], False, type(exc).__name__)
            LOGGER.warning(json.dumps({"event": "retention_deletion_failed",
                                       "resource_kind": item["resource_kind"],
                                       "error_type": type(exc).__name__}))
            failed += 1
    return {"deleted": deleted, "failed": failed}


def operational_summary() -> dict:
    return STORE.operational_summary()
