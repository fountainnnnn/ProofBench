"""ProofBench FastAPI service with authenticated, tenant-scoped resources."""
from __future__ import annotations

import json
import hmac
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import ipaddress
import inspect
import socket
import uuid
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from dotenv import load_dotenv

load_dotenv()

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import ValidationError

from server import runs
from server.schemas import (AuthSessionRequest, ChatRequest, ProviderKeyRequest, RunRequest,
                            SyntheticDatasetRequest)
from server.security import (Identity, auth_is_configured, auth_mode, authenticate,
                             authenticate_token, check_auth_mode, local_mode,
                             provider_credentials)
from server.storage import (MAX_CSV_BYTES, MAX_IMAGE_BYTES, MAX_IMAGES, MAX_TOTAL_BYTES,
                            UPLOADS_DIR, datasets,
                            validate_ground_truth, validate_image)

ROOT = runs.ROOT
# ProofBench executes real benchmarks only. Legacy runs persisted before this
# remain readable and are surfaced as historical synthetic evidence; nothing in
# the write path can produce a simulated run.
RUN_MODE = "real"
SYSTEM_SANDBOX_ENV = {
    "NOSANA_BASE_URL", "NOSANA_API_KEY", "NOSANA_MODEL",
    "DOUBLEWORD_BASE_URL", "DOUBLEWORD_API_KEY", "DOUBLEWORD_MODEL",
    "OPENAI_API_KEY", "OPENAI_VISION_MODEL",
    "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL",
}
# Orchestration-only provider settings. These are surfaced in the settings and
# readiness snapshots and reach the orchestrator's runtime environment, but no
# first-party adapter is ever entitled to them: every name here is covered by
# engine.agent.NEVER_SANDBOX_PREFIXES and absent from
# engine.builtin_adapters.SANDBOX_ELIGIBLE_CREDENTIALS, so a candidate cannot be
# granted one however it is named or what its generated source asks for.
SYSTEM_ORCHESTRATION_ENV = {
    "MOONSHOT_API_KEY", "KIMI_MODEL",
    "OPENROUTER_API_KEY", "OPENROUTER_BASE_URL", "OPENROUTER_MODEL",
}
BUILTIN_PROVIDER_HOSTS = {
    "api.deepseek.com", "api.doubleword.ai", "api.moonshot.ai", "openrouter.ai",
}
ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,95}_(?:API_KEY|BASE_URL|MODEL)$")
# Oxylabs authenticates with a username/password pair rather than an API key, so
# readiness asks for two names the pattern above cannot express. They are listed
# individually on purpose: a broader `_USERNAME`/`_PASSWORD` suffix rule would
# let a caller name any credential it liked.
EXTRA_PROVIDER_ENV_NAMES = frozenset({"OXYLABS_USERNAME", "OXYLABS_PASSWORD"})
SYNTHETIC_LOCK = threading.Lock()
RETENTION_STARTED = False
LOGGER = logging.getLogger("proofbench.api")
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
TENANT_DATASET_QUOTA_BYTES = int(os.environ.get(
    "PROOFBENCH_MAX_TENANT_DATASET_BYTES", str(1024 * 1024 * 1024)))
OPS_LOCK = threading.Lock()
OPS = {
    "requests": 0, "duration_ms_total": 0.0,
    "status_2xx": 0, "status_3xx": 0, "status_4xx": 0, "status_5xx": 0,
    "auth_rejections": 0, "quota_rejections": 0,
    "retention_failures": 0, "sandbox_reconciliation_failures": 0,
}


def _ops_increment(name: str, value=1) -> None:
    with OPS_LOCK:
        OPS[name] = OPS.get(name, 0) + value


def _ops_snapshot() -> dict:
    with OPS_LOCK:
        current = dict(OPS)
    count = max(1, current["requests"])
    current["duration_ms_average"] = round(current.pop("duration_ms_total") / count, 2)
    current.update(runs.operational_summary())
    return current


def _allowed_origins() -> list[str]:
    origins = []
    for raw in os.environ.get("PROOFBENCH_ALLOWED_ORIGINS", "http://localhost:5173").split(","):
        origin = raw.strip()
        if not origin:
            continue
        parsed = urlsplit(origin)
        if (origin == "*" or parsed.scheme not in {"http", "https"} or not parsed.netloc or
                parsed.username or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
            raise RuntimeError("PROOFBENCH_ALLOWED_ORIGINS must contain explicit HTTP(S) origins")
        origins.append(f"{parsed.scheme}://{parsed.netloc}")
    if not origins:
        raise RuntimeError("PROOFBENCH_ALLOWED_ORIGINS must contain at least one origin")
    return list(dict.fromkeys(origins))


def _run_retention() -> None:
    # 0 means no automatic expiry and is the local default: nothing the operator
    # created is deleted on a timer. Explicit deletions are still processed.
    days = int(os.environ.get("PROOFBENCH_RETENTION_DAYS", "0"))
    if not 0 <= days <= 3650:
        raise RuntimeError(
            "PROOFBENCH_RETENTION_DAYS must be 0 (no automatic expiry) or between 1 and 3650")
    if days:
        runs.cleanup_expired(days)
    deletion = runs.process_deletion_queue([runs.RUNS_DIR, UPLOADS_DIR])
    if deletion["failed"]:
        _ops_increment("retention_failures", deletion["failed"])


def _reconcile_sandboxes() -> None:
    if not (os.environ.get("DAYTONA_API_KEY") and
            os.environ.get("PROOFBENCH_RECONCILE_SANDBOXES_ON_STARTUP", "1") == "1"):
        return
    if not runs.acquire_leader("sandbox-reconciliation", 120):
        return
    contract = runs.active_job_contract()
    try:
        from engine.sandbox_pool import SandboxPool
        pool = SandboxPool(
            size=0, owner_key="startup-reconciler",
            ledger_path=os.environ.get(
                "PROOFBENCH_SANDBOX_LEDGER", os.path.join(runs.RUNS_DIR, "sandbox_ledger.sqlite3")),
            deployment=os.environ.get("PROOFBENCH_DEPLOYMENT_ID", "local"),
        )
        parameters = inspect.signature(pool.reconcile_orphans).parameters
        kwargs = {}
        if "active_owner_keys" in parameters:
            kwargs["active_owner_keys"] = set(contract["active_owner_keys"])
        if "orphan_before" in parameters:
            kwargs["orphan_before"] = contract["orphan_before"]
        if contract["active_owner_keys"] and not kwargs:
            LOGGER.info(json.dumps({"event": "sandbox_reconciliation_deferred",
                                    "active_owners": len(contract["active_owner_keys"])}))
            return
        report = pool.reconcile_orphans(**kwargs)
        failures = len(report.get("failures", []))
        if failures:
            _ops_increment("sandbox_reconciliation_failures", failures)
        LOGGER.info(json.dumps({"event": "sandbox_reconciliation",
                                "deleted": len(report.get("deleted", [])),
                                "failures": failures}))
    except Exception as exc:
        _ops_increment("sandbox_reconciliation_failures")
        LOGGER.warning(json.dumps({"event": "sandbox_reconciliation_failed",
                                   "error_type": type(exc).__name__}))


@asynccontextmanager
async def _lifespan(_app):
    global RETENTION_STARTED
    # Raises with the specific defect (mixed modes, or neither) rather than the
    # generic message below, so a misconfigured deployment fails loudly at boot.
    check_auth_mode()
    if not auth_is_configured():
        raise RuntimeError("production authentication is not configured")
    if local_mode():
        LOGGER.warning(json.dumps({
            "event": "local_tokenless_mode",
            "detail": ("PROOFBENCH_INSECURE_DEV=1: API writes require no token. "
                       "Bind the listener to 127.0.0.1 only. To expose this "
                       "deployment, unset it and set PROOFBENCH_API_KEYS."),
        }))
    if runs.acquire_leader("retention", 300):
        _run_retention()
    if not RETENTION_STARTED:
        RETENTION_STARTED = True

        def periodic():
            while True:
                time.sleep(20)
                try:
                    runs.STORE.heartbeat_worker()
                    runs.recover_stale_jobs()
                    runs.release_stale_dataset_reservations()
                    runs.process_deletion_queue([runs.RUNS_DIR, UPLOADS_DIR])
                    if int(time.time()) % (6 * 60 * 60) < 20 and runs.acquire_leader("retention", 300):
                        _run_retention()
                except Exception as exc:
                    _ops_increment("retention_failures")
                    LOGGER.warning(json.dumps({"event": "retention_cycle_failed",
                                               "error_type": type(exc).__name__}))

        threading.Thread(target=periodic, daemon=True, name="proofbench-retention").start()
    _reconcile_sandboxes()
    yield


def _secure_cookie(request: Request) -> bool:
    configured = os.environ.get("PROOFBENCH_COOKIE_SECURE", "auto").strip().lower()
    if configured in {"1", "true", "yes"}:
        return True
    if configured in {"0", "false", "no"}:
        return False
    if configured != "auto":
        raise HTTPException(status_code=503, detail="invalid cookie security configuration")
    forwarded = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().lower()
    return request.url.scheme == "https" or forwarded == "https"


def _same_origin(request: Request) -> bool:
    origin = request.headers.get("origin", "").strip()
    host = request.headers.get("host", "").strip().lower()
    if not origin or not host:
        return False
    parsed = urlsplit(origin)
    forwarded = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().lower()
    scheme = forwarded or request.url.scheme.lower()
    return (parsed.scheme.lower() == scheme and parsed.netloc.lower() == host and
            not parsed.username and parsed.path in {"", "/"} and
            not parsed.query and not parsed.fragment)

_dev_docs = os.environ.get("PROOFBENCH_INSECURE_DEV") == "1"
app = FastAPI(title="ProofBench", docs_url="/docs" if _dev_docs else None,
              redoc_url="/redoc" if _dev_docs else None,
              openapi_url="/openapi.json" if _dev_docs else None, lifespan=_lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Last-Event-ID", "X-API-Key"],
)


@app.middleware("http")
async def _tenant_request_quota(request: Request, call_next):
    bootstrap = request.method == "POST" and request.url.path == "/api/auth/session"
    auth_status = request.method == "GET" and request.url.path == "/api/auth/session"
    cookie_logout = (request.method == "DELETE" and request.url.path == "/api/auth/session" and
                     not request.headers.get("authorization") and
                     not request.headers.get("x-api-key") and
                     bool(request.cookies.get("proofbench_api_key")))
    if cookie_logout:
        if not _same_origin(request):
            return JSONResponse(status_code=403, content={"detail": "same-origin logout required"})
        try:
            authenticate_token(request.cookies["proofbench_api_key"])
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail},
                                headers=exc.headers)
        return await call_next(request)
    if (request.method == "OPTIONS" or request.url.path == "/api/live" or bootstrap or auth_status or
            not request.url.path.startswith("/api/")):
        return await call_next(request)
    try:
        identity = authenticate(request)
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail},
                            headers=exc.headers)
    if not runs.consume_request(identity.tenant_id):
        return JSONResponse(status_code=429, content={"detail": "tenant request quota reached"},
                            headers={"Retry-After": "60"})
    return await call_next(request)


@app.middleware("http")
async def _request_observability(request: Request, call_next):
    supplied = request.headers.get("x-request-id", "")
    request_id = supplied if REQUEST_ID_RE.fullmatch(supplied) else uuid.uuid4().hex
    request.state.request_id = request_id
    started = time.monotonic()
    response = await call_next(request)
    duration_ms = (time.monotonic() - started) * 1000
    _ops_increment("requests")
    _ops_increment("duration_ms_total", duration_ms)
    _ops_increment(f"status_{response.status_code // 100}xx")
    if response.status_code in {401, 403}:
        _ops_increment("auth_rejections")
    if response.status_code == 429:
        _ops_increment("quota_rejections")
    response.headers["X-Request-ID"] = request_id
    if request.url.path.startswith("/api/") and request.url.path != "/api/live":
        response.headers["Cache-Control"] = "no-store"
    route = getattr(request.scope.get("route"), "path", "unmatched")
    LOGGER.info(json.dumps({"event": "http_request", "request_id": request_id,
                            "method": request.method, "route": route,
                            "status": response.status_code,
                            "duration_ms": round(duration_ms, 2)}))
    return response


def _payload(model, body):
    try:
        return model.model_validate(body)
    except ValidationError as exc:
        safe_errors = [{"type": item.get("type", "value_error"),
                        "msg": "Invalid request value"}
                       for item in exc.errors(include_url=False)]
        raise HTTPException(status_code=422, detail=safe_errors) from exc


async def _json(request: Request) -> dict:
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="invalid JSON body") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="JSON body must be an object")
    return body


def provider_environment(tenant_id: str) -> dict[str, str]:
    values = {name: os.environ[name]
              for name in SYSTEM_SANDBOX_ENV | SYSTEM_ORCHESTRATION_ENV
              if os.environ.get(name)}
    if _runtime_credentials_enabled():
        values.update(provider_credentials.snapshot(tenant_id))
    return values


def _runtime_credentials_enabled() -> bool:
    return (os.environ.get("PROOFBENCH_INSECURE_DEV") == "1" and
            os.environ.get("PROOFBENCH_ALLOW_RUNTIME_CREDENTIALS", "0") == "1")


def _is_provider_env_name(name: str) -> bool:
    return bool(ENV_NAME_RE.fullmatch(name)) or name in EXTRA_PROVIDER_ENV_NAMES


def _validate_provider_setting(name: str, value: str) -> None:
    if not name.endswith("_BASE_URL"):
        return
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or
            parsed.password or parsed.query or parsed.fragment):
        raise HTTPException(status_code=422, detail="provider base URLs must be public HTTPS URLs")
    try:
        port = parsed.port
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="provider base URL port is invalid") from exc
    allow_nonstandard_port = (
        os.environ.get("PROOFBENCH_INSECURE_DEV") == "1" and
        os.environ.get("PROOFBENCH_ALLOW_PROVIDER_NONSTANDARD_PORTS") == "1")
    if port not in {None, 443} and not allow_nonstandard_port:
        raise HTTPException(status_code=422, detail="provider base URLs must use port 443")
    hostname = parsed.hostname.lower().rstrip(".")
    metadata_hosts = {"metadata.google.internal", "metadata", "instance-data.ec2.internal"}
    if hostname == "localhost" or hostname.endswith(".localhost") or hostname in metadata_hosts:
        raise HTTPException(status_code=422, detail="provider base URLs must not target localhost")
    allowlist = BUILTIN_PROVIDER_HOSTS | {item.strip().lower().rstrip(".") for item in os.environ.get(
        "PROOFBENCH_PROVIDER_HOST_ALLOWLIST", "").split(",") if item.strip()}
    if not any(hostname == item or (item.startswith("*.") and
                                    hostname.endswith(item[1:]) and
                                    hostname != item[2:])
               for item in allowlist):
        raise HTTPException(status_code=422, detail="provider base URL host is not allowed")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        try:
            addresses = {ipaddress.ip_address(item[4][0]) for item in socket.getaddrinfo(
                hostname, parsed.port or 443, type=socket.SOCK_STREAM)}
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=422,
                                detail="provider base URL host could not be safely resolved") from exc
    else:
        addresses = {address}
    if not addresses or any(not address.is_global for address in addresses):
        raise HTTPException(status_code=422, detail="provider base URLs must not target private addresses")


def _venv_python() -> str:
    candidate = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
    return candidate if os.path.exists(candidate) else "python"


def _session_or_404(session_id: str, identity: Identity) -> dict:
    session = runs.get_session(session_id, identity.tenant_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    return session


def _dataset_or_404(dataset_id: str, identity: Identity):
    dataset = datasets.get(dataset_id, identity.tenant_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="dataset not found")
    return dataset


def _bind_dataset(session: dict, dataset) -> None:
    runs.bind_dataset(session["id"], dataset.owner, dataset.id, dataset.path)


def _emit_chat_event(session_id: str, event: str, data: dict,
                     job_id: str | None = None) -> None:
    runs.emit(session_id, event, data, job_id)


def _requested_builtins(spec: dict) -> list[tuple[dict, str]]:
    """Spec candidates that opt in to a ProofBench first-party adapter."""
    from engine.builtin_adapters import is_builtin_adapter

    requested = []
    for candidate_spec in spec.get("candidates") or []:
        name = str(candidate_spec.get("name") or "")
        if candidate_spec.get("use_fallback", True) and is_builtin_adapter(name):
            requested.append((candidate_spec, name))
    return requested


def _unavailable_builtin_candidates(spec: dict, env: dict) -> list[dict]:
    """Built-in candidates blocked by unconfigured credentials, names only."""
    from engine.builtin_adapters import missing_credentials

    unavailable = []
    for _candidate_spec, name in _requested_builtins(spec):
        missing = missing_credentials(name, env)
        if missing:
            unavailable.append({"name": name, "missing": list(missing)})
    return unavailable


def _missing_spec_capabilities(spec: dict, env: dict) -> list[dict]:
    """Capabilities this exact spec needs before a run can be admitted.

    Provider readiness is intentionally spec-sensitive: an assessment does not
    need an orchestration model, while a generated extraction adapter does not
    get admitted unless code generation can be performed.  Return names only,
    never credential values.
    """
    from engine.builtin_adapters import is_builtin_adapter
    from engine.llm_clients import CAPABILITY_PROVIDERS, PROVIDERS, capability_providers

    # provider_environment is the run's trusted configuration snapshot.  Do
    # not consult arbitrary process variables here, or admission could promise
    # a capability different from the immutable environment supplied to the
    # run itself.
    merged_env = dict(env or {})
    required = set()
    if spec.get("benchmark_type") == "tool_assessment":
        required.add("assessment")
    else:
        for candidate in spec.get("candidates") or []:
            name = str(candidate.get("name") or "")
            uses_builtin = candidate.get("use_fallback", True) and is_builtin_adapter(name)
            if not uses_builtin:
                required.add("codegen")
                break
    missing = []
    for capability in sorted(required):
        if not capability_providers(capability, merged_env):
            missing.append({
                "capability": capability,
                "providers": list(CAPABILITY_PROVIDERS[capability]),
                "required_env": [PROVIDERS[name].api_key_env
                                 for name in CAPABILITY_PROVIDERS[capability]],
            })
    return missing


def _measured_metrics_from_artifact(run_dir: str) -> dict | None:
    """Read only the engine's authoritative measured artifact after late failure."""
    path = os.path.join(run_dir, "metrics.json")
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    metrics = payload.get("metrics")
    if payload.get("provenance") != "measured" or not isinstance(metrics, dict) or not metrics:
        return None
    return metrics


def _authorize_builtin_candidates(orch, spec: dict) -> dict:
    """Return an execution copy of the spec carrying built-in capability tokens.

    Entitlements are server-owned and capability-based: the adapter source is
    loaded from ProofBench's own tree, the exact credential names come from
    engine.builtin_adapters, and the orchestrator binds them to that specific
    adapter object. A candidate name alone never grants anything, and a
    generated adapter is never a capability holder.
    """
    import copy as _copy

    from engine.agent import TRUSTED_ADAPTER_TOKEN_FIELD
    from engine.builtin_adapters import entitled_credentials, load_builtin_candidate

    execution_spec = _copy.deepcopy(spec)
    env = orch.ctx.env_passthrough
    for candidate_spec, name in _requested_builtins(execution_spec):
        builtin = load_builtin_candidate(name)
        # The capability is matched against the spec's candidate name, which may
        # differ from the module's slug only in case.
        builtin.name = name
        builtin.docs_url = candidate_spec.get("docs_url") or builtin.docs_url
        builtin.pricing_url = candidate_spec.get("pricing_url") or builtin.pricing_url
        # The execution copy carries the resolved first-party URLs so docs
        # intelligence never scrapes an empty address when the client spec
        # omitted one. Only this private copy is amended; the persisted and
        # client-visible spec keeps whatever the client sent.
        if builtin.docs_url:
            candidate_spec["docs_url"] = builtin.docs_url
        if builtin.pricing_url:
            candidate_spec["pricing_url"] = builtin.pricing_url
        candidate_spec[TRUSTED_ADAPTER_TOKEN_FIELD] = orch.register_trusted_candidate(
            builtin, entitled_credentials(name, env))
    return execution_spec


def _fail_worker(session_id: str, exc: Exception, job_id: str | None = None) -> None:
    runs.emit(session_id, "error", {
        "message": f"Operation failed ({type(exc).__name__}). Check server logs and retry."}, job_id)


def _get_or_create_orchestrator(identity: Identity, session: dict, run_id: str | None = None,
                                chat_job_id: str | None = None):
    from engine.agent import Orchestrator, intake_system

    # A labelled dataset is what makes a scored extraction benchmark possible,
    # so intake is only allowed to propose one when the session has one bound.
    dataset_available = bool(session.get("dataset_path"))
    if run_id:
        directory = runs.run_dir(run_id, identity.tenant_id)
        return Orchestrator(
            run_id, directory,
            lambda event, data: runs.emit(session["id"], event, data, run_id),
            cancel_event=session["cancel_event"],
            provider_env=provider_environment(identity.tenant_id),
            dataset_available=dataset_available,
        )
    run_dir = runs.session_dir(session["id"], identity.tenant_id)
    os.makedirs(run_dir, exist_ok=True)
    orch = Orchestrator(
        session["id"], run_dir,
        lambda event, data: _emit_chat_event(session["id"], event, data, chat_job_id),
        cancel_event=session["cancel_event"],
        # Capture a fresh immutable snapshot for every job. Durable message history below
        # replaces process-local orchestrator state, so worker routing and secret rotation
        # cannot leave one process using stale tenant configuration.
        provider_env=provider_environment(identity.tenant_id),
        dataset_available=dataset_available,
    )
    history = list(session.get("messages") or [])
    if history and history[-1].get("role") == "user":
        history = history[:-1]
    orch._messages = [{"role": "system", "content": intake_system(dataset_available)}, *(
        {"role": item["role"], "content": item["text"]} for item in history
    )]
    return orch


@app.get("/api/live")
def api_live():
    """Unauthenticated process liveness only."""
    return {"status": "ok"}


# The bootstrap route runs before authentication, so its body is attacker
# controlled and unmetered by the per-tenant quota. A token is a few dozen
# bytes; 16 KiB is generous for the whole JSON envelope.
MAX_AUTH_BODY_BYTES = 16 * 1024
_LOGIN_WINDOW_SECONDS = 60.0
_LOGIN_FAILURE_LIMIT = 10
_LOGIN_CLIENT_LIMIT = 1024
_login_failures: dict[str, list[float]] = {}
_login_overflow_failures: list[float] = []
_login_lock = threading.Lock()


async def _bounded_body(request: Request, limit: int) -> bytes:
    """Read at most `limit` bytes, refusing before anything larger is buffered.

    `request.body()` buffers the whole payload in memory before any handler code
    can look at it, so the declared Content-Length is checked first and the
    stream is then consumed with a running total. The second check is what
    actually enforces the bound: Content-Length is a client claim, and a chunked
    request supplies none at all.
    """
    declared = request.headers.get("content-length", "").strip()
    if declared.isdigit() and int(declared) > limit:
        raise HTTPException(status_code=413, detail="request body too large")
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise HTTPException(status_code=413, detail="request body too large")
        chunks.append(chunk)
    return b"".join(chunks)


def _client_key(request: Request) -> str:
    """A throttle key derived only from the transport peer.

    `X-Forwarded-For` is deliberately not consulted: it is client-settable, so
    honouring it would let one attacker mint unlimited throttle buckets. Behind
    a reverse proxy every caller therefore shares one bucket, which is why the
    limit counts failures only and stays well above what a human retyping a
    token would produce.
    """
    host = (getattr(request.client, "host", "") or "").strip()
    try:
        return ipaddress.ip_address(host).compressed
    except ValueError:
        return "unknown"


def _login_throttled(key: str) -> bool:
    global _login_overflow_failures
    cutoff = time.monotonic() - _LOGIN_WINDOW_SECONDS
    with _login_lock:
        if key not in _login_failures and len(_login_failures) >= _LOGIN_CLIENT_LIMIT:
            _login_overflow_failures = [
                stamp for stamp in _login_overflow_failures if stamp > cutoff
            ]
            return len(_login_overflow_failures) >= _LOGIN_FAILURE_LIMIT
        recent = [stamp for stamp in _login_failures.get(key, []) if stamp > cutoff]
        if recent:
            _login_failures[key] = recent
        else:
            _login_failures.pop(key, None)
        return len(recent) >= _LOGIN_FAILURE_LIMIT


def _record_login_failure(key: str) -> None:
    global _login_overflow_failures
    now = time.monotonic()
    cutoff = now - _LOGIN_WINDOW_SECONDS
    with _login_lock:
        if key not in _login_failures and len(_login_failures) >= _LOGIN_CLIENT_LIMIT:
            for stale, stamps in list(_login_failures.items()):
                if not any(stamp > cutoff for stamp in stamps):
                    _login_failures.pop(stale, None)
        if key not in _login_failures and len(_login_failures) >= _LOGIN_CLIENT_LIMIT:
            recent = [stamp for stamp in _login_overflow_failures if stamp > cutoff]
            recent.append(now)
            _login_overflow_failures = recent[-_LOGIN_FAILURE_LIMIT:]
            return
        recent = [stamp for stamp in _login_failures.get(key, []) if stamp > cutoff]
        recent.append(now)
        _login_failures[key] = recent[-_LOGIN_FAILURE_LIMIT:]


@app.post("/api/auth/session")
async def api_auth_session(request: Request):
    """Exchange an API token for the HttpOnly cookie required by native EventSource."""
    # The tokenless local profile has no credential to guess, so throttling it
    # would only add a way to lock the single operator out of their own console.
    throttle_key = "" if local_mode() else _client_key(request)
    if throttle_key and _login_throttled(throttle_key):
        raise HTTPException(status_code=429, detail="too many sign-in attempts",
                            headers={"Retry-After": str(int(_LOGIN_WINDOW_SECONDS))})
    authorization = request.headers.get("authorization", "")
    bearer = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    raw_body = await _bounded_body(request, MAX_AUTH_BODY_BYTES)
    if raw_body:
        try:
            body = json.loads(raw_body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise HTTPException(status_code=400, detail="invalid JSON body") from exc
        if not isinstance(body, dict):
            raise HTTPException(status_code=422, detail="JSON body must be an object")
    else:
        body = {}
    payload = _payload(AuthSessionRequest, body) if body else None
    body_token = payload.token if payload else ""
    if bearer and body_token and not hmac.compare_digest(bearer, body_token):
        raise HTTPException(status_code=400, detail="header and body tokens do not match")
    try:
        authenticate_token(bearer or body_token)
    except HTTPException as exc:
        # Only credential rejections feed the throttle. A 503 is the
        # deployment's own misconfiguration and would otherwise let a broken
        # config lock out every client once it was fixed.
        if throttle_key and exc.status_code in {401, 403}:
            _record_login_failure(throttle_key)
        raise
    try:
        max_age = int(os.environ.get("PROOFBENCH_COOKIE_MAX_AGE", "28800"))
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="invalid cookie lifetime configuration") from exc
    if not 60 <= max_age <= 604800:
        raise HTTPException(status_code=503, detail="cookie lifetime must be between 60 and 604800 seconds")
    response = JSONResponse({"ok": True})
    response.set_cookie(
        "proofbench_api_key", bearer or body_token,
        max_age=max_age,
        httponly=True, secure=_secure_cookie(request), samesite="strict", path="/api",
    )
    return response


@app.get("/api/auth/session")
def api_auth_status(request: Request):
    """Probe cookie/header auth without turning an expected signed-out state into a 401.

    `auth_mode` is explicit so the console never has to infer which deployment
    profile it is talking to. In `local` mode every caller already resolves to
    the deterministic local tenant, so the console enters without a credential;
    in `authenticated` mode the two flags keep their fail-closed meaning.
    """
    # This route is public, so it is the one place a mixed configuration could
    # still be observed by a browser. Report the defect instead of resolving it
    # into the tokenless answer.
    try:
        check_auth_mode()
    except RuntimeError as exc:
        raise HTTPException(status_code=503,
                            detail="authentication configuration is invalid") from exc
    if local_mode():
        return {"auth_mode": "local", "cookie_authenticated": True, "write_authenticated": True}
    authorization = request.headers.get("authorization", "")
    bearer = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    bearer = bearer or request.headers.get("x-api-key", "").strip()
    cookie = request.cookies.get("proofbench_api_key", "").strip()

    def valid(token: str) -> bool:
        if not token:
            return False
        try:
            authenticate_token(token)
            return True
        except HTTPException:
            return False

    return {"auth_mode": auth_mode(), "cookie_authenticated": valid(cookie),
            "write_authenticated": valid(bearer)}


@app.delete("/api/auth/session")
def api_auth_logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie("proofbench_api_key", path="/api", httponly=True, samesite="strict")
    return response


@app.get("/api/ready")
def api_ready(identity: Identity = Depends(authenticate)):
    """Authenticated readiness: configuration and writable durable storage."""
    checks = {"authentication": auth_is_configured(), "runs_storage": False,
              "dataset_storage": False, "state_database": False}
    for key, path in (("runs_storage", runs.RUNS_DIR), ("dataset_storage", UPLOADS_DIR)):
        try:
            os.makedirs(path, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=path, prefix=".ready-", delete=True):
                pass
            checks[key] = True
        except OSError:
            checks[key] = False
    try:
        with runs.STORE.connect() as connection:
            checks["state_database"] = connection.execute("SELECT 1").fetchone()[0] == 1
    except Exception:
        checks["state_database"] = False
    if not all(checks.values()):
        raise HTTPException(status_code=503, detail={"status": "not_ready", "checks": checks})
    return {"status": "ready", "checks": checks}


@app.get("/api/health")
def api_health(identity: Identity = Depends(authenticate)):
    keys = ["DAYTONA_API_KEY", "MOONSHOT_API_KEY", "NOSANA_API_KEY",
            "DOUBLEWORD_API_KEY", "OXYLABS_USERNAME", "OXYLABS_PASSWORD", "OPENAI_API_KEY",
            "OPENROUTER_API_KEY"]
    tenant_keys = (set(provider_credentials.names(identity.tenant_id))
                   if _runtime_credentials_enabled() else set())
    return {"status": "ok", "version": "0.1.0",
            "keys": {name: bool(os.environ.get(name)) or name in tenant_keys for name in keys}}


# Provider readiness is derived from configuration only. Each entry names the
# variables a capability needs; no request is ever issued to a provider here, so
# loading this page cannot bill anyone.
PROVIDER_READINESS = (
    {"provider": "daytona", "label": "Daytona sandboxes",
     "capability": "Executes every benchmark candidate in an isolated sandbox.",
     "required": ("DAYTONA_API_KEY",), "optional": (), "essential": True},
    {"provider": "openai", "label": "OpenAI",
     "capability": "Orchestrator reasoning and the built-in openai_vision candidate.",
     "required": ("OPENAI_API_KEY",),
     "optional": ("OPENAI_ORCHESTRATOR_MODEL", "OPENAI_VISION_MODEL"), "essential": False},
    {"provider": "openrouter", "label": "OpenRouter",
     "capability": "OpenAI-compatible orchestration, documentation assessment, and reports.",
     "required": ("OPENROUTER_API_KEY",),
     "optional": ("OPENROUTER_MODEL", "OPENROUTER_BASE_URL"), "essential": False},
    {"provider": "moonshot", "label": "Moonshot / Kimi",
     "capability": "Optional preferred orchestrator and report writer.",
     "required": ("MOONSHOT_API_KEY",), "optional": ("KIMI_MODEL",), "essential": False},
    {"provider": "doubleword", "label": "Doubleword",
     "capability": "Batched documentation assessment and the built-in doubleword candidate.",
     "required": ("DOUBLEWORD_API_KEY", "DOUBLEWORD_MODEL"),
     "optional": ("DOUBLEWORD_BASE_URL",), "essential": False},
    {"provider": "deepseek", "label": "DeepSeek",
     "capability": "Generates and repairs adapters for candidates without a built-in.",
     "required": ("DEEPSEEK_API_KEY",),
     "optional": ("DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL"), "essential": False},
    {"provider": "oxylabs", "label": "Oxylabs",
     "capability": "Scrapes vendor documentation during intake and docs intelligence.",
     "required": ("OXYLABS_USERNAME", "OXYLABS_PASSWORD"), "optional": (), "essential": False},
    {"provider": "nosana", "label": "Nosana",
     "capability": "Optional built-in nosana_vlm candidate.",
     "required": ("NOSANA_BASE_URL", "NOSANA_API_KEY", "NOSANA_MODEL"),
     "optional": (), "essential": False},
)

# LLM work is capability based: any one of the listed providers satisfies the
# capability. This is what lets a deployment holding only OPENROUTER_API_KEY run
# a benchmark without OpenAI, DeepSeek, or Doubleword. Only capabilities marked
# essential can block a run.
CAPABILITY_READINESS = (
    {"capability": "orchestration",
     "label": "Orchestration and report writing",
     "detail": "Plans the run, drives tool calls, and writes the report.",
     "essential": True},
    {"capability": "assessment",
     "label": "Documentation assessment",
     "detail": "Rates tool documentation and integration feasibility.",
     "essential": False},
    {"capability": "codegen",
     "label": "Adapter generation",
     "detail": "Builds adapters for candidates without a built-in.",
     "essential": False},
)


@app.get("/api/providers")
def api_providers(identity: Identity = Depends(authenticate)):
    """Report configured/ready/missing per provider. Never returns secret values."""
    from engine.llm_clients import CAPABILITY_PROVIDERS, capability_providers

    env = provider_environment(identity.tenant_id)

    def configured(name: str) -> bool:
        return bool(str(env.get(name) or os.environ.get(name) or "").strip())

    providers = []
    for entry in PROVIDER_READINESS:
        missing = [name for name in entry["required"] if not configured(name)]
        present_optional = [name for name in entry["optional"] if configured(name)]
        if not entry["required"]:
            status = "ready"
        elif not missing:
            status = "ready"
        elif len(missing) < len(entry["required"]):
            status = "partial"
        else:
            status = "missing"
        providers.append({
            "provider": entry["provider"], "label": entry["label"],
            "capability": entry["capability"], "essential": entry["essential"],
            "status": status,
            "required": list(entry["required"]),
            "missing": missing,
            "optional_configured": present_optional,
        })

    # Resolution reads the same configuration the engine will, so what is
    # reported here is what a run would actually select. No request is issued.
    resolution_env = {name: str(env.get(name) or os.environ.get(name) or "")
                      for name in {*env, *os.environ}}
    capabilities = []
    for entry in CAPABILITY_READINESS:
        available = capability_providers(entry["capability"], resolution_env)
        capabilities.append({
            "capability": entry["capability"], "label": entry["label"],
            "detail": entry["detail"], "essential": entry["essential"],
            "status": "ready" if available else "missing",
            "selected": available[0] if available else None,
            "available": list(available),
            "candidates": list(CAPABILITY_PROVIDERS[entry["capability"]]),
        })

    blocked = [item["provider"] for item in providers
               if item["essential"] and item["status"] != "ready"]
    blocked += [item["capability"] for item in capabilities
                if item["essential"] and item["status"] != "ready"]
    return {"mode": RUN_MODE, "run_ready": not blocked,
            "blocked_by": blocked, "providers": providers,
            "capabilities": capabilities}


@app.get("/api/metrics")
def api_metrics(identity: Identity = Depends(authenticate)):
    # Fixed-cardinality operational counters only. Tenant identifiers and paths are omitted.
    return {"status": "ok", "metrics": _ops_snapshot()}


@app.get("/api/sessions")
def api_sessions(identity: Identity = Depends(authenticate)):
    return runs.list_sessions(identity.tenant_id)


@app.get("/api/sessions/{session_id}")
def api_session(session_id: str, identity: Identity = Depends(authenticate)):
    session = runs.public_session(session_id, identity.tenant_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    return session


@app.get("/api/settings/provider-keys")
def api_provider_keys(identity: Identity = Depends(authenticate)):
    runtime_writes_enabled = _runtime_credentials_enabled()
    tenant_names = (set(provider_credentials.names(identity.tenant_id))
                    if runtime_writes_enabled else set())
    system = {name for name in SYSTEM_SANDBOX_ENV | SYSTEM_ORCHESTRATION_ENV
              if os.environ.get(name)}
    return {"runtime_writes_enabled": runtime_writes_enabled,
            "managed_by": "runtime" if runtime_writes_enabled else "deployment",
            "keys": [
        *({"env": name, "source": "system"} for name in sorted(system - tenant_names)),
        *({"env": name, "source": "settings"} for name in sorted(tenant_names)),
    ]}


@app.post("/api/settings/provider-keys")
async def api_save_provider_key(request: Request, identity: Identity = Depends(authenticate)):
    if not _runtime_credentials_enabled():
        raise HTTPException(status_code=503,
                            detail="runtime credentials are disabled; configure deployment secrets")
    payload = _payload(ProviderKeyRequest, await _json(request))
    env = payload.env.upper()
    if not _is_provider_env_name(env):
        raise HTTPException(status_code=422,
                            detail="env must name a provider API_KEY, BASE_URL, MODEL, "
                                   "or an Oxylabs username or password")
    _validate_provider_setting(env, payload.value)
    provider_credentials.set(identity.tenant_id, env, payload.value)
    return {"env": env, "source": "settings"}


@app.delete("/api/settings/provider-keys/{env}")
def api_delete_provider_key(env: str, identity: Identity = Depends(authenticate)):
    if not _runtime_credentials_enabled():
        raise HTTPException(status_code=503,
                            detail="runtime credentials are disabled; configure deployment secrets")
    env = env.upper()
    if not _is_provider_env_name(env):
        raise HTTPException(status_code=422, detail="invalid environment variable name")
    provider_credentials.delete(identity.tenant_id, env)
    return {"ok": True}


@app.post("/api/sessions")
def api_create_session(identity: Identity = Depends(authenticate)):
    dataset = datasets.synthetic(identity.tenant_id)
    session = runs.new_session(identity.tenant_id)
    _bind_dataset(session, dataset)
    return {"session_id": session["id"], "title": session["title"]}


@app.delete("/api/sessions/{session_id}")
def api_delete_session(session_id: str, identity: Identity = Depends(authenticate)):
    try:
        deleted = runs.delete_session(session_id, identity.tenant_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="session not found")
    return {"session_id": session_id, "deleted": True}


@app.post("/api/sessions/{session_id}/stop")
def api_stop(session_id: str, identity: Identity = Depends(authenticate)):
    _session_or_404(session_id, identity)
    accepted = runs.request_stop(session_id)
    return {"session_id": session_id, "status": "stopping" if accepted else "not_running"}


@app.get("/api/runs/{run_id}/results")
def api_run_results(run_id: str, identity: Identity = Depends(authenticate)):
    resolved = runs.resolve_run_id(run_id, identity.tenant_id)
    result = runs.load_run_results(resolved, identity.tenant_id) if resolved else None
    if result is None:
        raise HTTPException(status_code=404, detail="run not found")
    return result


@app.get("/api/runs/{run_id}/report.pdf")
def api_run_pdf(run_id: str, download: bool = False,
                identity: Identity = Depends(authenticate)):
    resolved = runs.resolve_run_id(run_id, identity.tenant_id)
    if not resolved:
        raise HTTPException(status_code=404, detail="run not found")
    try:
        run_dir = runs.run_dir(resolved, identity.tenant_id)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc
    report_path = os.path.join(run_dir, "report.pdf")
    if (os.path.commonpath((os.path.realpath(run_dir), os.path.realpath(report_path))) !=
            os.path.realpath(run_dir) or not os.path.isfile(report_path)):
        raise HTTPException(status_code=404, detail="PDF report not found")
    return FileResponse(report_path, media_type="application/pdf",
                        filename=f"proofbench_{resolved}_report.pdf",
                        content_disposition_type="attachment" if download else "inline")


@app.get("/api/sessions/{session_id}/events")
def api_events(session_id: str, request: Request,
               identity: Identity = Depends(authenticate)):
    session = _session_or_404(session_id, identity)
    try:
        cursor = max(0, int(request.headers.get("last-event-id", "-1")) + 1)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid Last-Event-ID")
    target_job_id = session.get("active_job_id") or session.get("latest_job_id")

    def generate():
        nonlocal cursor
        last_ping = time.monotonic()
        while True:
            batch = runs.event_records_since(session_id, identity.tenant_id, cursor)
            if batch is None:
                break
            if not batch:
                time.sleep(0.5)
                if time.monotonic() - last_ping >= 15:
                    last_ping = time.monotonic()
                    yield ": ping\n\n"
                continue
            for sequence, event, data, _job_id in batch:
                cursor = sequence + 1
                yield f"id: {sequence}\nevent: {event}\ndata: {json.dumps(data)}\n\n"
            if target_job_id and any(event == "done" and job_id == target_job_id
                                     for _, event, _, job_id in batch):
                break
            if not target_job_id and batch[-1][1] == "done":
                current = runs.stream_state(session_id, identity.tenant_id)
                if current and not current["is_running"] and cursor >= current["event_seq"]:
                    break

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/chat")
async def api_chat(request: Request, identity: Identity = Depends(authenticate)):
    payload = _payload(ChatRequest, await _json(request))
    requested_dataset = (_dataset_or_404(payload.dataset_id, identity)
                         if payload.dataset_id else None)
    if payload.session_id:
        session = _session_or_404(payload.session_id, identity)
    else:
        dataset = requested_dataset or datasets.synthetic(identity.tenant_id)
        title = (payload.message[:40] + "...") if len(payload.message) > 40 else payload.message
        session = runs.new_session(identity.tenant_id, title=title)
    sid = session["id"]
    selected_dataset = requested_dataset
    if selected_dataset is None and session.get("dataset_id"):
        selected_dataset = _dataset_or_404(session["dataset_id"], identity)
    if selected_dataset is None:
        selected_dataset = datasets.synthetic(identity.tenant_id)
    try:
        claimed = runs.begin_chat(sid, identity.tenant_id, selected_dataset.id,
                                  RUN_MODE, payload.message)
    except runs.BusyError as exc:
        raise HTTPException(status_code=409, detail="session already working") from exc
    except runs.QuotaError as exc:
        raise HTTPException(status_code=429, detail=str(exc), headers={"Retry-After": "60"}) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="session or dataset not found") from exc
    session = runs.get_session(sid, identity.tenant_id)

    def worker():
        failed = False
        with runs.job_heartbeat(sid, claimed["id"]):
            try:
                _get_or_create_orchestrator(
                    identity, session, chat_job_id=claimed["id"]).chat(payload.message)
            except Exception as exc:
                failed = True
                _fail_worker(sid, exc, claimed["id"])
            finally:
                runs.finish_run(sid, cancelled=runs.is_cancelled(sid), failed=failed,
                                emit_done=True, job_id=claimed["id"])

    threading.Thread(target=worker, daemon=True).start()
    return {"session_id": sid}


@app.get("/api/datasets")
def api_list_datasets(identity: Identity = Depends(authenticate)):
    return {"datasets": [{"id": item["id"], "dataset_id": item["id"],
                           "kind": item["kind"], "image_count": item["image_count"],
                           "total_bytes": item["total_bytes"], "created_at": item["created_at"]}
                          for item in runs.list_datasets(identity.tenant_id)]}


@app.delete("/api/datasets/{dataset_id}")
def api_delete_dataset(dataset_id: str, identity: Identity = Depends(authenticate)):
    dataset = _dataset_or_404(dataset_id, identity)
    try:
        claimed = runs.begin_dataset_delete(dataset_id, identity.tenant_id)
    except runs.BusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not claimed:
        raise HTTPException(status_code=404, detail="dataset not found")
    success = False
    try:
        success = datasets.delete(dataset.id, identity.tenant_id)
    except (OSError, ValueError) as exc:
        LOGGER.warning(json.dumps({"event": "dataset_delete_deferred",
                                   "error_type": type(exc).__name__}))
        raise HTTPException(status_code=409, detail="dataset deletion queued for retry") from exc
    finally:
        runs.finish_dataset_delete(dataset_id, success)
    if not success:
        raise HTTPException(status_code=404, detail="dataset not found")
    return {"dataset_id": dataset_id, "deleted": True}


@app.post("/api/datasets")
async def api_datasets(request: Request, images: list[UploadFile] | None = File(default=None),
                       ground_truth: UploadFile | None = File(default=None),
                       identity: Identity = Depends(authenticate)):
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" not in content_type.lower():
        payload = _payload(SyntheticDatasetRequest, await _json(request))
        if payload.use_synthetic:
            try:
                with SYNTHETIC_LOCK:
                    demo_path = os.path.join(ROOT, "data", "demo")
                    image_path = os.path.join(demo_path, "images")
                    ready = (os.path.isfile(os.path.join(demo_path, "ground_truth.csv")) and
                             os.path.isdir(image_path) and any(
                                 os.path.splitext(name)[1].lower() in {".png", ".jpg", ".jpeg", ".webp"}
                                 for name in os.listdir(image_path)))
                    if not ready:
                        subprocess.run(
                            [_venv_python(), "make_dataset.py", "--out", "data/demo", "--n", "15"],
                            cwd=ROOT, check=True, capture_output=True, text=True, timeout=300)
            except (OSError, subprocess.SubprocessError) as exc:
                raise HTTPException(status_code=500, detail="synthetic dataset generation failed") from exc
            dataset = datasets.synthetic(identity.tenant_id)
            return {"dataset_id": dataset.id}

    uploads = [upload for upload in (images or []) if upload and upload.filename]
    if not uploads or len(uploads) > MAX_IMAGES:
        raise HTTPException(status_code=422, detail=f"between 1 and {MAX_IMAGES} images are required")
    if not ground_truth or not ground_truth.filename:
        raise HTTPException(status_code=422, detail="ground_truth.csv is required")
    if os.path.basename(ground_truth.filename) != ground_truth.filename or ground_truth.filename.lower() != "ground_truth.csv":
        raise HTTPException(status_code=422, detail="ground truth filename must be ground_truth.csv")
    if ground_truth.content_type and ground_truth.content_type.lower() not in {
        "text/csv", "application/csv", "application/vnd.ms-excel", "application/octet-stream"
    }:
        raise HTTPException(status_code=422, detail="ground truth must have a CSV MIME type")

    image_values: list[tuple[str, bytes]] = []
    image_ids: set[str] = set()
    total_bytes = 0
    try:
        for upload in uploads:
            data = await upload.read(MAX_IMAGE_BYTES + 1)
            total_bytes += len(data)
            if total_bytes > MAX_TOTAL_BYTES:
                raise ValueError("total upload size exceeded")
            doc_id = validate_image(upload.filename, upload.content_type, data)
            if doc_id in image_ids:
                raise ValueError("duplicate image document id")
            image_ids.add(doc_id)
            image_values.append((os.path.basename(upload.filename), data))
        csv_data = await ground_truth.read(MAX_CSV_BYTES + 1)
        total_bytes += len(csv_data)
        if total_bytes > MAX_TOTAL_BYTES:
            raise ValueError("total upload size exceeded")
        validate_ground_truth(csv_data, image_ids)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="uploaded dataset is invalid") from exc

    def destination_for(dataset_id: str) -> str:
        destination = os.path.realpath(os.path.join(UPLOADS_DIR, dataset_id))
        if os.path.commonpath((os.path.realpath(UPLOADS_DIR), destination)) != os.path.realpath(UPLOADS_DIR):
            raise ValueError("invalid server upload path")
        return destination
    try:
        reservation = runs.reserve_dataset(identity.tenant_id, destination_for, total_bytes,
                                           TENANT_DATASET_QUOTA_BYTES)
    except runs.QuotaError as exc:
        raise HTTPException(status_code=429, detail=str(exc), headers={"Retry-After": "60"}) from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="dataset reservation failed") from exc
    dataset_id = reservation["id"]
    destination = reservation["path"]
    created = False
    try:
        image_dir = os.path.join(destination, "images")
        os.makedirs(image_dir, exist_ok=False)
        created = True
        for filename, data in image_values:
            with open(os.path.join(image_dir, filename), "xb") as handle:
                handle.write(data)
        with open(os.path.join(destination, "ground_truth.csv"), "xb") as handle:
            handle.write(csv_data)
        runs.activate_dataset(dataset_id, identity.tenant_id, len(image_values), total_bytes)
    except (OSError, ValueError) as exc:
        runs.release_dataset_reservation(dataset_id, identity.tenant_id)
        if created and os.path.isdir(destination):
            shutil.rmtree(destination)
        raise HTTPException(status_code=500, detail="dataset could not be persisted") from exc
    except KeyError as exc:
        runs.release_dataset_reservation(dataset_id, identity.tenant_id)
        if created and os.path.isdir(destination):
            shutil.rmtree(destination)
        raise HTTPException(status_code=503, detail="dataset reservation expired") from exc
    return {"dataset_id": dataset_id}


@app.post("/api/sessions/{session_id}/run")
async def api_run(session_id: str, request: Request,
                  identity: Identity = Depends(authenticate)):
    session = _session_or_404(session_id, identity)
    payload = _payload(RunRequest, await _json(request))
    assessment = payload.spec.benchmark_type == "tool_assessment"
    dataset = None
    if not assessment:
        requested = payload.spec.dataset
        dataset_id = requested.dataset_id if requested and requested.dataset_id else session.get("dataset_id")
        if not dataset_id:
            raise HTTPException(status_code=422, detail="a server-issued dataset_id is required")
        dataset = _dataset_or_404(dataset_id, identity)
        if requested and requested.path and os.path.realpath(requested.path) != os.path.realpath(dataset.path):
            raise HTTPException(status_code=422, detail="client-supplied dataset paths are not accepted")
    spec = payload.spec.model_dump(exclude_none=True)
    if dataset:
        spec["dataset"] = {"path": dataset.path}
    provider_env = provider_environment(identity.tenant_id)
    missing_capabilities = _missing_spec_capabilities(spec, provider_env)
    if missing_capabilities:
        raise HTTPException(status_code=503, detail={
            "error": "provider_capability_unavailable",
            "message": "This benchmark cannot start until its required provider capabilities are configured.",
            "capabilities": missing_capabilities,
        })
    # Fail closed before any session or run mutation: a first-party candidate
    # whose credentials this deployment has not configured is unavailable, not
    # silently degraded.
    unavailable = _unavailable_builtin_candidates(spec, provider_env)
    if unavailable:
        raise HTTPException(status_code=422, detail={
            "error": "candidate_unavailable",
            "message": "These candidates need provider credentials this deployment has not configured.",
            "candidates": unavailable,
        })
    try:
        claimed = runs.begin_benchmark(session_id, identity.tenant_id, spec, RUN_MODE,
                                       dataset.id if dataset else None)
    except runs.BusyError as exc:
        raise HTTPException(status_code=409, detail="session already working")
    except runs.QuotaError as exc:
        raise HTTPException(status_code=429, detail=str(exc), headers={"Retry-After": "60"}) from exc
    run_id = claimed["id"]
    session = runs.get_session(session_id, identity.tenant_id)

    def worker():
        failed = False
        with runs.job_heartbeat(session_id, run_id):
            try:
                run_dir = runs.run_dir(run_id, identity.tenant_id)
                os.makedirs(run_dir, exist_ok=True)
                orch = _get_or_create_orchestrator(identity, session, run_id)
                # The capability tokens live only in this private execution copy,
                # never in the persisted or client-visible spec.
                metrics = orch.run_benchmark(_authorize_builtin_candidates(orch, spec))
                report_path = os.path.realpath(os.path.join(run_dir, "report.md"))
                report_md = ""
                if (os.path.commonpath((os.path.realpath(run_dir), report_path)) ==
                        os.path.realpath(run_dir) and os.path.isfile(report_path)):
                    with open(report_path, encoding="utf-8") as handle:
                        report_md = handle.read()
                runs.persist_run(run_id, spec=spec, metrics=metrics,
                                 report_md=report_md, citations=orch.ctx.citations,
                                 provenance="measured")
            except Exception as exc:
                if not runs.is_cancelled(session_id):
                    measured = _measured_metrics_from_artifact(run_dir) if "run_dir" in locals() else None
                    persisted = False
                    if measured is not None:
                        # Rendering may fail after the engine has durably
                        # measured results. Persist that evidence and expose the
                        # absent report rather than marking the benchmark failed.
                        try:
                            runs.persist_run(run_id, spec=spec, metrics=measured,
                                             report_md="",
                                             citations=orch.ctx.citations if "orch" in locals() else [],
                                             provenance="measured")
                            persisted = True
                        except Exception:
                            # Recovery itself failed, so the run has no durable
                            # evidence and must be reported as failed, not completed.
                            persisted = False
                    if persisted:
                        runs.emit(session_id, "artifact", {
                            "kind": "report", "available": False,
                            "warning": "Report rendering failed; measured metrics are available.",
                            "provenance": "measured",
                        }, run_id)
                    else:
                        failed = True
                        runs.emit(session_id, "error", {
                            "message": f"Operation failed ({type(exc).__name__}). Check server logs and retry."
                        }, run_id)
            finally:
                runs.finish_run(session_id, cancelled=runs.is_cancelled(session_id), failed=failed,
                                emit_done=True, run_id=run_id)

    threading.Thread(target=worker, daemon=True).start()
    return {"session_id": session_id, "run_id": run_id, "status": "started"}
