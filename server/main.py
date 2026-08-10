"""ProofBench FastAPI service with authenticated, tenant-scoped resources."""
from __future__ import annotations

import csv
import json
import hmac
import logging
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import ipaddress
import inspect
import socket
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

load_dotenv()

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import ValidationError

from engine import session_title
from server import brand, runs
from server.schemas import (AuthSessionRequest, ChatRequest, IntegrationAgentMessageRequest,
                            DefaultsRequest, ProviderKeyRequest,
                            ProviderKeyRevealRequest,
                            GenerateDatasetRequest,
                            RunRequest, ScraperOrderRequest,
                            SettingOptionsRequest,
                            SyntheticDatasetRequest)
from server.security import (Identity, auth_is_configured, auth_mode, authenticate,
                             authenticate_token, check_auth_mode, is_secret_env,
                             local_mode, mask_secret, provider_credentials)
from server.storage import (MAX_CSV_BYTES, MAX_IMAGE_BYTES, MAX_IMAGES, MAX_TOTAL_BYTES,
                            UPLOADS_DIR, datasets,
                            validate_ground_truth, validate_image)

ROOT = runs.ROOT
WEB_ROOT = os.environ.get("PROOFBENCH_WEB_ROOT", "").strip()
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
    "SUPERVISOR_PROVIDER", "SUPERVISOR_MODEL",
}
SYSTEM_SCRAPER_ENV = {
    "OXYLABS_USERNAME", "OXYLABS_PASSWORD",
    "SCRAPEDO_API_TOKEN",
    "BRIGHTDATA_API_TOKEN", "BRIGHTDATA_SERP_ZONE", "BRIGHTDATA_UNLOCKER_ZONE",
}
# Provisioning credentials the operator manages but no candidate ever receives:
# the Daytona key creates the sandboxes, so injecting it INTO one would hand a
# candidate the keys to the kingdom. Kept out of provider_environment for that
# reason, but still shown and revealable in Settings, where an operator expects
# to see the key a "ready" Daytona row is reading.
SYSTEM_PROVISION_ENV = {"DAYTONA_API_KEY"}
# Every provider credential the Settings page may list or reveal. This is a
# superset of what reaches a sandbox: it adds the provisioning keys above.
SETTINGS_PROVIDER_ENV = (SYSTEM_SANDBOX_ENV | SYSTEM_ORCHESTRATION_ENV
                         | SYSTEM_SCRAPER_ENV | SYSTEM_PROVISION_ENV)
BUILTIN_PROVIDER_HOSTS = {
    "api.deepseek.com", "api.doubleword.ai", "api.moonshot.ai", "openrouter.ai",
}
ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,95}_(?:API_KEY|BASE_URL|MODEL)$")
# Oxylabs authenticates with a username/password pair rather than an API key, so
# readiness asks for two names the pattern above cannot express. They are listed
# individually on purpose: a broader `_USERNAME`/`_PASSWORD` suffix rule would
# let a caller name any credential it liked.
EXTRA_PROVIDER_ENV_NAMES = frozenset({
    # A provider selector is configuration rather than a credential, but it
    # belongs in the same tenant-scoped provider snapshot as SUPERVISOR_MODEL.
    "SUPERVISOR_PROVIDER",
    "OXYLABS_USERNAME", "OXYLABS_PASSWORD",
    # The other two scrapers authenticate with a token plus a zone name, neither
    # of which the pattern above can express. Listed individually for the same
    # reason as the Oxylabs pair: a broader suffix rule would let a caller name
    # any credential it liked.
    "SCRAPEDO_API_TOKEN",
    "BRIGHTDATA_API_TOKEN", "BRIGHTDATA_SERP_ZONE", "BRIGHTDATA_UNLOCKER_ZONE",
})
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
    railway_domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "").strip()
    if railway_domain:
        parsed = urlsplit(f"https://{railway_domain}")
        if (not parsed.hostname or parsed.netloc != railway_domain or parsed.username or
                parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
            raise RuntimeError("RAILWAY_PUBLIC_DOMAIN is invalid")
        origins.append(f"https://{railway_domain}")
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
    # Bound to the runs accessors rather than to the store behind them, so a
    # test that swaps the database still writes through to the live one.
    provider_credentials.bind(
        loader=runs.provider_keys,
        saver=runs.set_provider_key,
        remover=runs.delete_provider_key,
    )
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
                    # Sandboxes leaked by a killed worker sit STOPPED on the
                    # provider and still count against the account's memory
                    # budget, starving every later run. Reconciliation is
                    # lease-guarded and owner-scoped, so running it on a cycle
                    # (not only at startup) reclaims them within minutes.
                    if int(time.time()) % (10 * 60) < 20:
                        _reconcile_sandboxes()
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
    if (request.method == "OPTIONS" or request.url.path in {"/api/live", "/api/deploy-ready"} or
            bootstrap or auth_status or
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
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
        # The bundler inlines the display face as a data: URI, so 'self' alone
        # blocks the console's own font and headings fall back to a system serif.
        "font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; "
        "form-action 'self'"
    )
    forwarded = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().lower()
    if request.url.scheme == "https" or forwarded == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
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


# Mirrors engine.llm_clients._PIN_ENV: the env name each capability's default
# is published under. Kept here so the settings layer never imports the engine's
# private mapping.
DEFAULT_PROVIDER_ENV = {
    "orchestration": "PROOFBENCH_DEFAULT_ORCHESTRATION_PROVIDER",
    "assessment": "PROOFBENCH_DEFAULT_ASSESSMENT_PROVIDER",
    "codegen": "PROOFBENCH_DEFAULT_CODEGEN_PROVIDER",
}


def provider_environment(tenant_id: str) -> dict[str, str]:
    from engine import scrapers

    values = {name: os.environ[name]
              for name in SYSTEM_SANDBOX_ENV | SYSTEM_ORCHESTRATION_ENV | SYSTEM_SCRAPER_ENV
              if os.environ.get(name)}
    values.update(provider_credentials.snapshot(tenant_id))
    # The engine reads all its configuration from this snapshot, so the stored
    # scraper preference travels the same path as everything else and a run uses
    # the order that was set when it started.
    values[scrapers.ORDER_ENV] = " ".join(runs.scraper_order(tenant_id))
    # Default-provider pins travel the same path, so /api/providers reports the
    # provider a run would actually select without asking a second source.
    for capability, chosen in runs.default_providers(tenant_id).items():
        env_name = DEFAULT_PROVIDER_ENV.get(capability)
        if env_name:
            values[env_name] = chosen
    return values


def _is_provider_env_name(name: str) -> bool:
    return bool(ENV_NAME_RE.fullmatch(name)) or name in EXTRA_PROVIDER_ENV_NAMES


def _validate_provider_setting(name: str, value: str) -> None:
    if name == "SUPERVISOR_PROVIDER":
        allowed = {"openai", "moonshot", "kimi", "openrouter", "deepseek", "doubleword"}
        if value.strip().casefold() not in allowed:
            raise HTTPException(
                status_code=422,
                detail="supervisor provider must name a supported provider",
            )
        return
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
    # Windows lays the venv interpreter out under Scripts/, POSIX under bin/.
    # Fall back to the interpreter running this server rather than a bare
    # "python", which is absent on systems that only ship python3.
    for parts in (("Scripts", "python.exe"), ("bin", "python")):
        candidate = os.path.join(ROOT, ".venv", *parts)
        if os.path.exists(candidate):
            return candidate
    return sys.executable or "python"


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
    # The client is told to check the server logs, so the server has to put
    # something in them. It did not: a run that died mid-evaluation reported
    # only its exception class, and the traceback existed nowhere at all, which
    # left no way to tell an unreadable dataset from a broken adapter.
    # The traceback is operator-only; the client message stays sanitized
    # because it is rendered to whoever is holding the session.
    LOGGER.exception(json.dumps({"event": "job_failed", "session_id": session_id,
                                 "job_id": job_id, "error": type(exc).__name__}))
    runs.emit(session_id, "error", {
        "message": f"Operation failed ({type(exc).__name__}). Check server logs and retry."}, job_id)


def _run_summary(session: dict) -> str:
    """A factual account of the session's finished run, for follow-up questions.

    The intake agent otherwise has only the message history, so asking it why a
    candidate lost produced speculation about a run it could not see.
    """
    metrics = session.get("results")
    if not isinstance(metrics, dict) or not metrics:
        return ""
    spec = session.get("spec") if isinstance(session.get("spec"), dict) else {}
    lines = [
        "CONTEXT: this session has already completed a benchmark. The measured "
        "results are below. Answer questions about them from these numbers only. "
        "Never invent a metric, and never describe a candidate whose status is "
        "no_result as having scored badly: it produced no result at all, for the "
        "stated reason.",
    ]
    objective = str(spec.get("objective") or spec.get("category") or "").strip()
    if objective:
        lines.append(f"Objective: {objective}")
    for name, row in metrics.items():
        if not isinstance(row, dict):
            continue
        readable = ", ".join(
            f"{key}={row[key]}"
            for key in ("exact_accuracy", "field_f1", "cer", "mean_latency_s",
                        "failure_rate", "cost_per_1k_docs", "setup_complexity",
                        "rating", "n_docs", "documents_scored", "status")
            if key in row and row[key] is not None
        )
        entry = f"- {name}: {readable or 'no metrics recorded'}"
        if row.get("status") == "no_result":
            entry += f" | did not run: {row.get('error_summary') or 'reason not recorded'}"
        lines.append(entry)
    return "\n".join(lines)


def _dataset_schema(dataset_path: str | None) -> list[dict] | None:
    """The labelled schema of a bound dataset, as [{name, type}, ...].

    A generated dataset declares its types in ``schema.json``; anything else
    falls back to the ground-truth CSV header with legacy type inference, so
    the sample invoice dataset keeps meaning what it always meant.
    """
    if not dataset_path:
        return None
    from engine.fields import FIELD_TYPES, infer_type

    manifest = os.path.join(dataset_path, "schema.json")
    if os.path.isfile(manifest):
        try:
            with open(manifest, encoding="utf-8") as handle:
                declared = json.load(handle)
            fields = [
                {"name": str(item["name"]),
                 "type": item.get("type") if item.get("type") in FIELD_TYPES
                 else infer_type(str(item["name"]))}
                for item in declared if isinstance(item, dict) and item.get("name")
            ]
            if fields:
                return fields
        except (OSError, ValueError, KeyError, TypeError):
            pass  # fall through to the CSV header
    truth = os.path.join(dataset_path, "ground_truth.csv")
    try:
        with open(truth, encoding="utf-8-sig", newline="") as handle:
            header = next(csv.reader(handle), [])
    except (OSError, StopIteration):
        return None
    names = [name.strip() for name in header if name.strip() and name.strip() != "doc_id"]
    return [{"name": name, "type": infer_type(name)} for name in names] or None


def _get_or_create_orchestrator(identity: Identity, session: dict, run_id: str | None = None,
                                chat_job_id: str | None = None):
    from engine.agent import Orchestrator, intake_system

    # Not a gate on which benchmark intake may propose — the question decides
    # that. This says only whether labelled examples already exist, which is
    # what pins the spec's schema to columns that have ground truth; without
    # them intake declares the schema and the run builds examples to match.
    dataset_available = bool(session.get("dataset_path"))
    dataset_fields = _dataset_schema(session.get("dataset_path"))
    if run_id:
        directory = runs.run_dir(run_id, identity.tenant_id)
        return Orchestrator(
            run_id, directory,
            lambda event, data: runs.emit(session["id"], event, data, run_id),
            cancel_event=session["cancel_event"],
            provider_env=provider_environment(identity.tenant_id),
            dataset_available=dataset_available,
            dataset_fields=dataset_fields,
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
        dataset_fields=dataset_fields,
        run_summary=_run_summary(session),
    )
    history = list(session.get("messages") or [])
    if history and history[-1].get("role") == "user":
        history = history[:-1]
    orch._messages = [{"role": "system", "content": intake_system(dataset_available, dataset_fields)}, *(
        {"role": item["role"], "content": item["text"]} for item in history
    )]
    # Durable message history carries only what was said. Research lives in tool
    # results, which are not persisted, so without this the agent re-discovered
    # the same candidates on every turn.
    orch.prior_findings = runs.list_findings(session["id"])
    return orch


def _latest_reply(session_id: str, owner: str) -> str:
    """The answer this turn just produced, read back from durable history.

    Read from the store rather than the orchestrator's private message list, so
    a turn that ended early still titles from whatever the user actually saw.
    """
    session = runs.get_session(session_id, owner) or {}
    for message in reversed(session.get("messages") or []):
        if (message or {}).get("role") == "assistant":
            return str(message.get("text") or "")
    return ""


def _is_unnamed(session: dict) -> bool:
    """True until the session has been answered once.

    A session is titled exactly once, on its first exchange: renaming it every
    turn would make the sidebar shift under the user as they typed.
    """
    return not any((message or {}).get("role") == "assistant"
                   for message in (session or {}).get("messages") or [])


def _retitle(session_id: str, identity: Identity, message: str, orchestrator) -> None:
    """Name the session after its first exchange. Never fails the chat."""
    try:
        title = session_title.summarize_title(
            message, _latest_reply(session_id, identity.tenant_id),
            env=getattr(orchestrator, "runtime_env", None))
        runs.set_value(session_id, "title", title)
    except Exception:
        LOGGER.info(json.dumps({"event": "session_retitle_skipped"}))


@app.get("/api/live")
def api_live():
    """Unauthenticated process liveness only."""
    return {"status": "ok"}


def _readiness_checks() -> dict[str, bool]:
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
    return checks


@app.get("/api/deploy-ready")
def api_deploy_ready():
    """Secret-free deployment readiness for Railway's headerless probe."""
    if not all(_readiness_checks().values()):
        raise HTTPException(status_code=503, detail="not ready")
    return {"status": "ready"}


@app.get("/api/storage")
def api_storage(identity: Identity = Depends(authenticate)):
    """Whether the paths this deployment writes to are actually durable.

    A managed volume that is configured but not mounted is invisible from the
    outside: the app writes happily to the container filesystem and the files
    are simply gone after the next deploy, while the database rows that
    describe them survive — so the console lists a dataset whose images 404.
    This reports what the process can actually see, which is the only account
    that settles it.
    """
    import shutil

    def describe(path: str) -> dict:
        real = os.path.realpath(path)
        exists = os.path.isdir(real)
        usage = shutil.disk_usage(real) if exists else None
        return {
            "path": real,
            "exists": exists,
            # A mount point is a directory whose device differs from its
            # parent's. On an unmounted volume this is False and everything
            # written there dies with the container.
            "is_mount": os.path.ismount(real) if exists else False,
            "total_gib": round(usage.total / 1024 ** 3, 2) if usage else None,
            "free_gib": round(usage.free / 1024 ** 3, 2) if usage else None,
            "entries": len(os.listdir(real)) if exists else 0,
        }

    runtime = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "")
    return {
        "railway_volume_mount_path": runtime or None,
        "runtime_root": describe(runtime or "/app/runtime"),
        "dataset_root": describe(UPLOADS_DIR),
        "runs_root": describe(runs.RUNS_DIR),
    }


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
    checks = _readiness_checks()
    if not all(checks.values()):
        raise HTTPException(status_code=503, detail={"status": "not_ready", "checks": checks})
    return {"status": "ready", "checks": checks}


@app.get("/api/health")
def api_health(identity: Identity = Depends(authenticate)):
    keys = ["DAYTONA_API_KEY", "MOONSHOT_API_KEY", "NOSANA_API_KEY",
            "DOUBLEWORD_API_KEY", "OXYLABS_USERNAME", "OXYLABS_PASSWORD", "OPENAI_API_KEY",
            "OPENROUTER_API_KEY"]
    tenant_keys = set(provider_credentials.names(identity.tenant_id))
    return {"status": "ok", "version": "0.1.0",
            "keys": {name: bool(os.environ.get(name)) or name in tenant_keys for name in keys}}


# Provider readiness is derived from configuration only. Each entry names the
# variables a capability needs; no request is ever issued to a provider here, so
# loading this page cannot bill anyone.
#
# Only the prose and the product decisions live here. Which providers exist, and
# which variables each one reads, are derived from the engine registries in
# `_provider_readiness` below. Hand-listing both drifted: Moonshot, Scrape.do,
# and Bright Data were fully implemented and individually accepted by the
# credentials endpoint, yet absent from Settings, so the only way to configure
# one was to add it by hand as though it were a service ProofBench did not know.
PROVIDER_NOTES = {
    "openai": {
        # Essential by product decision, not by technical necessity: the
        # capability layer below would accept any configured LLM, but OpenAI is
        # the default a deployment is expected to hold, so a run is blocked
        # without it.
        "label": "OpenAI", "essential": True,
        "capability": "Orchestrator reasoning and the built-in openai_vision candidate.",
        "optional": ("OPENAI_VISION_MODEL",),
    },
    "openrouter": {
        "label": "OpenRouter",
        "capability": "OpenAI-compatible orchestration, documentation assessment, and reports.",
    },
    "doubleword": {
        "label": "Doubleword",
        "capability": "Batched documentation assessment and the built-in doubleword candidate.",
        # A gateway serves many models, so choosing the provider is not yet a
        # choice of model: this one is required rather than optional.
        "required": ("DOUBLEWORD_MODEL",),
    },
    "deepseek": {
        "label": "DeepSeek",
        "capability": "Generates and repairs adapters for candidates without a built-in.",
    },
    "moonshot": {
        "label": "Moonshot (Kimi)",
        "capability": "Orchestration and supervisor reasoning with Kimi models.",
    },
}


def _provider_readiness() -> tuple[dict, ...]:
    """One Settings row per service this deployment can actually hold a key for.

    Everything the engine implements appears, configured or not, so an operator
    never has to add a provider ProofBench already ships. A scraper that needs
    no credential (the self-hosted pair) is left out: there is nothing to enter,
    and its liveness is reported by the scraper chain card instead.
    """
    from engine import scrapers
    from engine.llm_clients import PROVIDERS

    rows = [{"provider": "daytona", "label": "Daytona sandboxes",
             "capability": "Executes every benchmark candidate in an isolated sandbox.",
             "required": ("DAYTONA_API_KEY",), "optional": (), "essential": True}]

    for name, spec in PROVIDERS.items():
        notes = PROVIDER_NOTES.get(name, {})
        extra_required = tuple(notes.get("required", ()))
        optional = tuple(
            item for item in (spec.model_env, spec.base_url_env, *notes.get("optional", ()))
            if item and item not in extra_required)
        rows.append({
            "provider": name,
            "label": notes.get("label", name.replace("_", " ").title()),
            "capability": notes.get("capability", "An OpenAI-compatible LLM provider."),
            "required": (spec.api_key_env, *extra_required),
            "optional": optional,
            "essential": bool(notes.get("essential", False)),
        })

    for name in scrapers.DEFAULT_ORDER:
        meta = scrapers.META.get(name, {})
        credentials = tuple(meta.get("credentials", ()))
        if not credentials:
            continue
        rows.append({
            "provider": name,
            "label": scrapers.LABELS.get(name, name.title()),
            "capability": meta.get(
                "hint", "Scrapes vendor documentation during intake and docs intelligence."),
            "required": credentials,
            "optional": (),
            "essential": False,
        })

    return tuple(rows)


PROVIDER_READINESS = _provider_readiness()

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
    # Scraping is one-of-N like the LLM capabilities, so it belongs here rather
    # than in PROVIDER_READINESS, whose entries AND together every env a single
    # vendor needs. Intake cannot find candidates without one of these.
    {"capability": "scraping",
     "label": "Documentation scraping",
     "detail": "Finds and reads vendor documentation during intake.",
     "essential": True},
)

# Scraping resolves through engine.scrapers, not the LLM capability registry.
SCRAPING_CAPABILITY = "scraping"


@app.get("/api/providers")
def api_providers(identity: Identity = Depends(authenticate)):
    """Report configured/ready/missing per provider. Never returns secret values."""
    from engine import scrapers
    from engine.llm_clients import (
        CAPABILITY_PROVIDERS,
        capability_providers,
        primary_identity,
        supervisor_identity,
    )

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
            # The full optional set, not just what is set: the Settings card
            # offers these names as choices, so an operator can pick a variable
            # that is not configured yet instead of recalling its spelling.
            "optional": list(entry["optional"]),
            "missing": missing,
            "optional_configured": present_optional,
        })

    # Resolution reads the same configuration the engine will, so what is
    # reported here is what a run would actually select. No request is issued.
    resolution_env = {name: str(env.get(name) or os.environ.get(name) or "")
                      for name in {*env, *os.environ}}
    capabilities = []
    for entry in CAPABILITY_READINESS:
        if entry["capability"] == SCRAPING_CAPABILITY:
            # Scrapers live in their own registry, and a provider counts as
            # available if it can serve either half of the job. The emitted
            # shape is identical to an LLM capability so nothing downstream
            # has to special-case it.
            order = list(scrapers.DEFAULT_ORDER)
            found = (set(scrapers.configured_providers(resolution_env, "search")) |
                     set(scrapers.configured_providers(resolution_env, "scrape")))
            available = tuple(name for name in scrapers.order_from_env(resolution_env)
                              if name in found)
            candidates = order
        else:
            available = capability_providers(entry["capability"], resolution_env)
            candidates = list(CAPABILITY_PROVIDERS[entry["capability"]])
        capabilities.append({
            "capability": entry["capability"], "label": entry["label"],
            "detail": entry["detail"], "essential": entry["essential"],
            "status": "ready" if available else "missing",
            "selected": available[0] if available else None,
            "available": list(available),
            "candidates": candidates,
        })

    # Independent supervision is a capability of its own: a DISTINCT reviewer for
    # the checkpoints that correct a model's own output. It never blocks a run —
    # a single-provider deployment stays fully functional — but the console shows
    # whether an independent second model is actually configured, and if not, the
    # exact env that would supply one. It is derived from configuration only and
    # reveals provider/model labels, never a credential.
    supervision = []
    for supervised in ("orchestration", "assessment"):
        primary = primary_identity(supervised, resolution_env)
        # Assessment falls back across its whole configured chain, so the
        # self-check refuses any reviewer drawn from it — the console reports the
        # SAME resolution a run would actually make, never a false independence.
        exclude_providers = (
            capability_providers("assessment", resolution_env)
            if supervised == "assessment" else None
        )
        reviewer = supervisor_identity(
            supervised, resolution_env, exclude_providers=exclude_providers)
        supervision.append({
            "supervises": supervised,
            "primary": primary.label() if primary else None,
            "reviewer": reviewer.label() if reviewer else None,
            "independent": reviewer is not None,
            "config": ["SUPERVISOR_PROVIDER", "SUPERVISOR_MODEL"],
        })

    # What the engine already falls back to when a model or base URL is unset.
    # A model id and a URL are values nobody recalls, so the console offers the
    # known-good one as a starting point instead of an empty box. Credentials
    # are deliberately absent: there is no default for a secret.
    from engine.llm_clients import PROVIDERS as LLM_PROVIDERS

    setting_defaults = {}
    for spec in LLM_PROVIDERS.values():
        setting_defaults[spec.model_env] = spec.default_model
        if spec.base_url_env:
            setting_defaults[spec.base_url_env] = spec.default_base_url

    blocked = [item["provider"] for item in providers
               if item["essential"] and item["status"] != "ready"]
    blocked += [item["capability"] for item in capabilities
                if item["essential"] and item["status"] != "ready"]
    return {"mode": RUN_MODE, "run_ready": not blocked,
            "blocked_by": blocked, "providers": providers,
            "capabilities": capabilities, "supervision": supervision,
            "setting_defaults": setting_defaults}


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
    tenant_names = set(provider_credentials.names(identity.tenant_id))
    system = {name for name in SETTINGS_PROVIDER_ENV if os.environ.get(name)}
    # A mask is the tail of a secret and nothing else. Non-secret settings
    # (model ids, base URLs, the supervisor selector) carry no mask at all
    # rather than their value: this listing must stay provably free of any
    # readable configured value, and only the reveal endpoint hands one back.
    def entry(name: str, source: str) -> dict:
        value = (provider_credentials.get(identity.tenant_id, name)
                 if source == "settings" else os.environ.get(name))
        secret = is_secret_env(name)
        return {"env": name, "source": source,
                "masked": mask_secret(value) if secret else None,
                "secret": secret,
                "revealable": True}

    return {"keys": [
        *(entry(name, "system") for name in sorted(system - tenant_names)),
        *(entry(name, "settings") for name in sorted(tenant_names)),
    ]}


def _scraper_payload(tenant_id: str) -> dict:
    """The provider chain, and which links actually hold credentials."""
    from engine import scrapers

    env = provider_environment(tenant_id)
    merged = {**os.environ, **env}
    order = runs.scraper_order(tenant_id)
    ready = set(scrapers.configured_providers(merged, "search")) | set(
        scrapers.configured_providers(merged, "scrape"))
    return {"order": list(order), "default": list(scrapers.DEFAULT_ORDER),
            "providers": [{"name": name, "configured": name in ready,
                           **scrapers.provider_meta(name, merged)} for name in order]}


def _defaults_payload(tenant_id: str) -> dict:
    """Which provider serves each capability, and which the operator picked.

    `pinned` is the stored choice and `selected` is what a run would actually
    use. They differ when the pinned provider has no key yet, which is a state
    worth showing rather than rejecting.
    """
    from engine.llm_clients import (CAPABILITY_PROVIDERS, PROVIDERS,
                                    capability_providers, provider_configured)

    env = provider_environment(tenant_id)
    resolution_env = {name: str(env.get(name) or os.environ.get(name) or "")
                      for name in {*env, *os.environ}}
    pins = runs.default_providers(tenant_id)
    labels = {item["provider"]: item["label"] for item in PROVIDER_READINESS}
    detail = {item["capability"]: item for item in CAPABILITY_READINESS}

    llm = []
    for capability in runs.PINNABLE_CAPABILITIES:
        available = capability_providers(capability, resolution_env)
        llm.append({
            "capability": capability,
            "label": detail[capability]["label"],
            "detail": detail[capability]["detail"],
            "pinned": pins.get(capability),
            "selected": available[0] if available else None,
            # model_env travels with the option because a gateway provider
            # serves many models: choosing OpenRouter is not a choice of model,
            # so the picker has to be able to ask for one.
            "options": [
                {"name": name,
                 "label": labels.get(name, name.replace("_", " ").title()),
                 "configured": provider_configured(name, resolution_env),
                 "model_env": PROVIDERS[name].model_env,
                 "model": (resolution_env.get(PROVIDERS[name].model_env)
                           or PROVIDERS[name].default_model),
                 "model_is_default": not resolution_env.get(PROVIDERS[name].model_env)}
                for name in CAPABILITY_PROVIDERS[capability]
                if name in PROVIDERS
            ],
        })
    return {"llm": llm, "scrapers": _scraper_payload(tenant_id)}


@app.get("/api/settings/defaults")
def api_defaults(identity: Identity = Depends(authenticate)):
    return _defaults_payload(identity.tenant_id)


@app.put("/api/settings/defaults")
async def api_set_defaults(request: Request, identity: Identity = Depends(authenticate)):
    payload = _payload(DefaultsRequest, await _json(request))
    for capability in runs.PINNABLE_CAPABILITIES:
        chosen = getattr(payload, capability)
        # Absent means "leave alone"; an explicit empty string clears the pin.
        if chosen is None:
            continue
        try:
            runs.set_default_provider(identity.tenant_id, capability, chosen or None)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if payload.scraper_order is not None:
        runs.set_scraper_order(identity.tenant_id, payload.scraper_order)
    return _defaults_payload(identity.tenant_id)


@app.get("/api/settings/scrapers")
def api_scraper_order(identity: Identity = Depends(authenticate)):
    return _scraper_payload(identity.tenant_id)


def _integration_actions(tenant_id: str):
    """The writes the integration agent may perform, bound to one tenant.

    Every one of these is something the operator can already do in the tab the
    agent runs in, and each goes through the same name check and value
    validation as the endpoint behind that UI. The agent therefore gains no
    authority its caller lacks, and a mistake it makes is refused by the same
    code that would refuse the operator.

    Rejections come back as ValueError rather than HTTPException on purpose: the
    agent is mid-turn and reads the reason, so a wrong variable name becomes a
    correction rather than a 422 that ends the conversation.
    """
    from engine import integration_tools

    def check(env: str, value: str | None = None) -> None:
        if not _is_provider_env_name(env):
            raise ValueError(
                f"{env} is not a provider setting this deployment accepts; it must name "
                "an API key, base URL, model, or one of the documented scraper variables")
        if value is not None:
            try:
                _validate_provider_setting(env, value)
            except HTTPException as exc:
                raise ValueError(str(exc.detail)) from exc

    class SettingsActions(integration_tools.Actions):
        def save_credential(self, env: str, value: str) -> str:
            check(env, value)
            if not is_secret_env(env):
                raise ValueError(f"{env} is not a credential; use save_setting for it")
            provider_credentials.set(tenant_id, env, value)
            LOGGER.warning(json.dumps({"event": "integration_agent_wrote_credential",
                                       "env": env}))
            return f"Stored the operator's key as {env}."

        def save_setting(self, env: str, value: str) -> str:
            check(env, value)
            # A secret must arrive through save_credential and the vault. If it
            # reached this call the model was holding the value itself, which is
            # the exact condition the vault exists to prevent.
            if is_secret_env(env):
                raise ValueError(
                    f"{env} holds a secret and cannot be set this way; ask the operator "
                    "to paste it, then use save_credential")
            provider_credentials.set(tenant_id, env, value)
            return f"Set {env} to {value}."

        def remove_setting(self, env: str) -> str:
            check(env)
            provider_credentials.delete(tenant_id, env)
            return f"Cleared {env}."

        def set_scraper_order(self, order: list[str]) -> str:
            stored = runs.set_scraper_order(tenant_id, order)
            return "Scraper order is now " + " ".join(stored) + "."

        def environment(self) -> dict[str, str]:
            return provider_environment(tenant_id)

    return SettingsActions()


@app.get("/api/settings/integration-agent")
def api_integration_agent_status(identity: Identity = Depends(authenticate)):
    """Report the mandatory agent prerequisites without contacting providers."""
    from engine import integration_agent

    return integration_agent.readiness(provider_environment(identity.tenant_id))


@app.post("/api/settings/integration-agent/messages")
def api_integration_agent_message(
    payload: IntegrationAgentMessageRequest,
    identity: Identity = Depends(authenticate),
):
    """Research one provider integration without applying or activating code."""
    from engine import integration_agent

    env = provider_environment(identity.tenant_id)
    state = integration_agent.readiness(env)
    if not state["ready"]:
        raise HTTPException(status_code=409, detail={
            "error": "integration_agent_unavailable",
            "message": "Configure one default LLM and one web scraping API first.",
            "missing": state["missing"],
        })
    try:
        return integration_agent.respond(
            payload.message,
            env,
            [item.model_dump() for item in payload.history],
            actions=_integration_actions(identity.tenant_id),
        )
    except ValueError as exc:
        LOGGER.warning(json.dumps({
            "event": "integration_agent_invalid_response",
            "error_type": type(exc).__name__,
        }))
        raise HTTPException(
            status_code=502,
            detail="The integration agent returned an invalid response.",
        ) from exc
    except Exception as exc:
        LOGGER.warning(json.dumps({
            "event": "integration_agent_failed",
            "error_type": type(exc).__name__,
        }))
        raise HTTPException(
            status_code=502,
            detail="The integration agent could not complete this request.",
        ) from exc


@app.post("/api/settings/integration-agent/stream")
def api_integration_agent_stream(
    payload: IntegrationAgentMessageRequest,
    identity: Identity = Depends(authenticate),
):
    """Same research as the messages endpoint, narrating each step as it runs.

    The work is synchronous and blocking, so it runs on a worker thread and
    publishes progress through a queue the response generator drains. The
    client shows those steps while waiting and drops them once `result`
    arrives, so the transcript keeps only the answer.
    """
    from engine import integration_agent

    env = provider_environment(identity.tenant_id)
    state = integration_agent.readiness(env)
    if not state["ready"]:
        raise HTTPException(status_code=409, detail={
            "error": "integration_agent_unavailable",
            "message": "Configure one default LLM and one web scraping API first.",
            "missing": state["missing"],
        })

    history = [item.model_dump() for item in payload.history]
    message = payload.message
    updates: "queue.Queue[tuple[str, object]]" = queue.Queue()

    def work():
        try:
            answer = integration_agent.respond(
                message,
                env,
                history,
                on_progress=lambda event: updates.put(("progress", event)),
                actions=_integration_actions(identity.tenant_id),
            )
            updates.put(("result", answer))
        except ValueError as exc:
            LOGGER.warning(json.dumps({
                "event": "integration_agent_invalid_response",
                "error_type": type(exc).__name__,
            }))
            updates.put(("error", "The integration agent returned an invalid response."))
        except Exception as exc:
            LOGGER.warning(json.dumps({
                "event": "integration_agent_failed",
                "error_type": type(exc).__name__,
            }))
            updates.put(("error", "The integration agent could not complete this request."))
        finally:
            updates.put(("end", None))

    def generate():
        worker = threading.Thread(target=work, daemon=True)
        worker.start()
        while True:
            try:
                event, data = updates.get(timeout=15)
            except queue.Empty:
                yield ": ping\n\n"
                continue
            if event == "end":
                break
            yield f"event: {event}\ndata: {json.dumps(data)}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.put("/api/settings/scrapers")
async def api_set_scraper_order(request: Request, identity: Identity = Depends(authenticate)):
    payload = _payload(ScraperOrderRequest, await _json(request))
    return {"order": list(runs.set_scraper_order(identity.tenant_id, payload.order))}


@app.post("/api/settings/provider-keys")
async def api_save_provider_key(request: Request, identity: Identity = Depends(authenticate)):
    payload = _payload(ProviderKeyRequest, await _json(request))
    env = payload.env.upper()
    if not _is_provider_env_name(env):
        raise HTTPException(status_code=422,
                            detail="env must name a provider API_KEY, BASE_URL, MODEL, "
                                   "or an Oxylabs username or password")
    _validate_provider_setting(env, payload.value)
    provider_credentials.set(identity.tenant_id, env, payload.value)
    return {"env": env, "source": "settings"}


@app.post("/api/settings/setting-options")
async def api_setting_options(request: Request, identity: Identity = Depends(authenticate)):
    """Research the values one non-secret provider setting can take.

    Secrets are rejected before any research runs: a model id and a base URL are
    published facts, an API key is not, and this endpoint must never look like a
    place to obtain one.
    """
    from engine import integration_agent

    payload = _payload(SettingOptionsRequest, await _json(request))
    env_name = payload.env.upper()
    if not _is_provider_env_name(env_name) or is_secret_env(env_name):
        raise HTTPException(status_code=422,
                            detail="only a provider model or base URL setting can be researched")

    env = provider_environment(identity.tenant_id)
    state = integration_agent.readiness(env)
    if not state["ready"]:
        raise HTTPException(status_code=409, detail={
            "error": "integration_agent_unavailable",
            "message": "Configure one default LLM and one web scraping API first.",
            "missing": state["missing"],
        })
    try:
        return integration_agent.suggest_values(env_name, env)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        LOGGER.warning(json.dumps({
            "event": "setting_options_failed",
            "error_type": type(exc).__name__,
        }))
        raise HTTPException(
            status_code=502,
            detail="The integration agent could not research that setting.",
        ) from exc


@app.post("/api/settings/provider-keys/reveal")
async def api_reveal_provider_key(request: Request, identity: Identity = Depends(authenticate)):
    """Return one credential in plaintext, for an operator who asked to see it.

    POST rather than GET on purpose: `authenticate` accepts the session cookie
    only for GET/HEAD, so requiring POST forces a real Authorization header on
    the one endpoint that hands back a secret, and keeps the value out of URLs,
    referrers, and any caching layer.
    """
    payload = _payload(ProviderKeyRevealRequest, await _json(request))
    env = payload.env.upper()
    if not _is_provider_env_name(env):
        raise HTTPException(status_code=422, detail="invalid environment variable name")

    value = provider_credentials.get(identity.tenant_id, env)
    source = "settings"
    if value is None:
        # Falling back to the deployment environment is what makes this useful:
        # a fresh deployment holds every key in .env, and a reveal that only
        # ever saw runtime overrides would answer nothing on the first visit.
        value = os.environ.get(env) if env in SETTINGS_PROVIDER_ENV else None
        source = "system"
    if not value:
        raise HTTPException(status_code=404, detail="no value is stored for that setting")

    # The name is worth an audit line; the value never is.
    LOGGER.warning(json.dumps({"event": "provider_credential_revealed",
                               "env": env, "source": source}))
    return JSONResponse({"env": env, "source": source, "value": value},
                        headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


@app.delete("/api/settings/provider-keys/{env}")
def api_delete_provider_key(env: str, identity: Identity = Depends(authenticate)):
    env = env.upper()
    if not _is_provider_env_name(env):
        raise HTTPException(status_code=422, detail="invalid environment variable name")
    provider_credentials.delete(identity.tenant_id, env)
    return {"ok": True}


_LOGOS = brand.LogoCache(os.path.join(runs.RUNS_DIR, "brand"))
# One page of results, not a crawl budget.
_MAX_LOGO_NAMES = 24


@app.get("/api/brand")
def api_brand(names: str = "", identity: Identity = Depends(authenticate)):
    """Vendor marks for candidates this tenant has benchmarked.

    Resolved at request time and cached, so a tool benchmarked five minutes ago
    has its logo without anyone re-running a build script. Names not in the
    tenant's own specs are ignored outright: the endpoint resolves from a
    candidate's stored docs URL and never from anything a caller supplies.
    """
    known = runs.candidate_docs_urls(identity.tenant_id)
    wanted = [name for name in str(names or "").split(",")[:_MAX_LOGO_NAMES] if name in known]
    logos = {}
    for name in wanted:
        found = _LOGOS.get(name, known[name])
        if found:
            logos[name] = brand.data_uri(*found)
    return {"logos": logos}


@app.post("/api/sessions")
def api_create_session(identity: Identity = Depends(authenticate)):
    # A new session starts with nothing bound. Auto-binding the invoice sample
    # made every session look to intake like an OCR session with an invoice
    # schema already chosen, which is how document extraction became the
    # implicit default for questions that were never about documents. What a
    # run measures is now decided by the question; the sample is one dataset a
    # user can reach for, not the one every session begins holding.
    session = runs.new_session(identity.tenant_id)
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


def _bind_dataset(session: dict, dataset) -> None:
    runs.bind_dataset(session["id"], dataset.owner, dataset.id, dataset.path)


@app.post("/api/chat")
async def api_chat(request: Request, identity: Identity = Depends(authenticate)):
    payload = _payload(ChatRequest, await _json(request))
    requested_dataset = (_dataset_or_404(payload.dataset_id, identity)
                         if payload.dataset_id else None)
    created_session = not payload.session_id
    if payload.session_id:
        session = _session_or_404(payload.session_id, identity)
    else:
        # Provisional only: the sidebar needs a label before the turn has said
        # anything worth naming. _retitle replaces it once the turn has.
        session = runs.new_session(identity.tenant_id,
                                   title=session_title.fallback_title(payload.message))
    sid = session["id"]
    # No fallback to the sample dataset. A conversation needs no labelled data,
    # and binding some anyway told intake this was a document session with its
    # schema already chosen — so every question arrived pre-shaped as OCR.
    selected_dataset = requested_dataset
    if selected_dataset is None and session.get("dataset_id"):
        selected_dataset = _dataset_or_404(session["dataset_id"], identity)
    try:
        claimed = runs.begin_chat(sid, identity.tenant_id,
                                  selected_dataset.id if selected_dataset else None,
                                  RUN_MODE, payload.message)
    except runs.BusyError as exc:
        if created_session:
            runs.delete_session(sid, identity.tenant_id)
        raise HTTPException(status_code=409, detail="session already working") from exc
    except runs.QuotaError as exc:
        if created_session:
            runs.delete_session(sid, identity.tenant_id)
        raise HTTPException(status_code=429, detail=str(exc), headers={"Retry-After": "60"}) from exc
    except KeyError as exc:
        if created_session:
            runs.delete_session(sid, identity.tenant_id)
        raise HTTPException(status_code=404, detail="session or dataset not found") from exc
    session = runs.get_session(sid, identity.tenant_id)
    # Snapshot taken before the turn runs: a session that has never been
    # answered is the one still wearing a provisional title.
    unnamed = _is_unnamed(session)

    def worker():
        failed = False
        with runs.job_heartbeat(sid, claimed["id"]):
            orchestrator = None
            try:
                orchestrator = _get_or_create_orchestrator(
                    identity, session, chat_job_id=claimed["id"])
                orchestrator.chat(payload.message)
            except Exception as exc:
                failed = True
                _fail_worker(sid, exc, claimed["id"])
            finally:
                # Saved even on failure: a turn that searched and then broke has
                # still learned something, and losing it is what made the next
                # turn start over.
                if orchestrator is not None and orchestrator.findings:
                    try:
                        runs.add_findings(sid, orchestrator.findings)
                    except Exception:
                        # Research is an optimisation; never fail a chat over it.
                        pass
                # The terminal event tells the browser to refresh its session
                # list. Persist the first generated title before that event, or
                # the refresh races ahead and keeps the provisional opening
                # message until a second turn happens to refresh it again.
                if unnamed and not failed:
                    _retitle(sid, identity, payload.message, orchestrator)
                runs.finish_run(sid, cancelled=runs.is_cancelled(sid), failed=failed,
                                emit_done=True, job_id=claimed["id"])

    threading.Thread(target=worker, daemon=True).start()
    return {"session_id": sid}


def _dataset_title(item: dict) -> str:
    """A generated dataset's human title, read from its manifest.

    Rows otherwise render as interchangeable "AI-generated dataset" entries and
    the user is left telling receipts from badges by hex id.
    """
    if item.get("kind") != "generated":
        return ""
    manifest_path = os.path.join(str(item.get("path") or ""), "manifest.json")
    try:
        with open(manifest_path, encoding="utf-8") as handle:
            loaded = json.load(handle)
        return str(loaded.get("title") or "")[:120] if isinstance(loaded, dict) else ""
    except (OSError, ValueError):
        return ""


@app.get("/api/datasets")
def api_list_datasets(identity: Identity = Depends(authenticate)):
    return {"datasets": [{"id": item["id"], "dataset_id": item["id"],
                           "kind": item["kind"], "image_count": item["image_count"],
                           "total_bytes": item["total_bytes"], "created_at": item["created_at"],
                           "title": _dataset_title(item)}
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


# How many documents a run builds for itself. Smaller than a dataset a user
# designs deliberately: this one is built mid-run, and the wait before the
# benchmark starts is time the user did not ask to spend.
GENERATED_RUN_DOCS = 10


def _spec_field_schema(spec) -> list[dict]:
    """The measured spec's declared columns, as {"name", "type"} pairs.

    The evaluator matches ground truth to predictions by column name, so
    examples built for a spec must carry exactly the spec's own schema.
    """
    from engine.fields import infer_type

    schema = []
    for field in spec.fields:
        if isinstance(field, str):
            schema.append({"name": field, "type": infer_type(field)})
        else:
            schema.append({"name": field.name, "type": field.type})
    return schema


def _dataset_design_prompt(session: dict, spec) -> str:
    """What to build examples of, in the user's own words plus the spec's.

    The session's opening message says what the user is actually trying to
    settle; the category and columns say what the run will score. The designer
    needs both — the words alone under-determine the documents, and the columns
    alone lose the domain.
    """
    said = ""
    for message in (session.get("messages") or []):
        if (message or {}).get("role") == "user":
            said = str(message.get("text") or "").strip()
            break
    columns = ", ".join(field["name"] for field in _spec_field_schema(spec))
    parts = [f"Benchmark category: {spec.category}", f"Fields to be scored: {columns}"]
    if said:
        parts.append(f"What the user asked for: {said[:1500]}")
    return "\n".join(parts)


def _generate_dataset(prompt: str, n: int, identity: Identity,
                      fields: list | None = None) -> str:
    """Design, render, and register labelled examples. Returns the dataset id.

    Shared by the explicit console action and by a measured run that reached
    start without data bound: both need exactly the same designed-then-rendered
    dataset, and having one path means a generated dataset is registered, quota-
    accounted, and cleaned up on failure the same way however it was asked for.
    ``fields`` pins the schema when the benchmark spec already declares one.
    """
    from engine import dataset_gen

    try:
        proposal = dataset_gen.propose_dataset(
            prompt, n, env=provider_environment(identity.tenant_id), fields=fields)
    except Exception as exc:
        LOGGER.warning(json.dumps({"event": "dataset_proposal_failed",
                                   "error_type": type(exc).__name__}))
        raise HTTPException(
            status_code=503,
            detail="the dataset designer could not produce a valid proposal; retry or upload a dataset",
        ) from exc

    def destination_for(dataset_id: str) -> str:
        destination = os.path.realpath(os.path.join(UPLOADS_DIR, dataset_id))
        if os.path.commonpath((os.path.realpath(UPLOADS_DIR), destination)) != os.path.realpath(UPLOADS_DIR):
            raise ValueError("invalid server upload path")
        return destination

    estimated_bytes = 40_000 * len(proposal["rows"])
    try:
        reservation = runs.reserve_dataset(identity.tenant_id, destination_for,
                                           estimated_bytes, TENANT_DATASET_QUOTA_BYTES)
    except runs.QuotaError as exc:
        raise HTTPException(status_code=429, detail=str(exc), headers={"Retry-After": "60"}) from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="dataset reservation failed") from exc
    dataset_id = reservation["id"]
    destination = reservation["path"]
    created = False
    try:
        os.makedirs(destination, exist_ok=False)
        created = True
        rendered = dataset_gen.render_dataset(proposal, destination, source_prompt=prompt)
        total_bytes = sum(f.stat().st_size for f in Path(destination).rglob("*") if f.is_file())
        runs.activate_dataset(dataset_id, identity.tenant_id,
                              len(rendered["doc_ids"]), total_bytes, kind="generated")
    except (OSError, ValueError) as exc:
        runs.release_dataset_reservation(dataset_id, identity.tenant_id)
        if created and os.path.isdir(destination):
            shutil.rmtree(destination)
        raise HTTPException(status_code=500, detail="generated dataset could not be persisted") from exc
    except KeyError as exc:
        runs.release_dataset_reservation(dataset_id, identity.tenant_id)
        if created and os.path.isdir(destination):
            shutil.rmtree(destination)
        raise HTTPException(status_code=503, detail="dataset reservation expired") from exc
    return dataset_id


@app.post("/api/datasets/generate")
def api_generate_dataset(payload: GenerateDatasetRequest,
                         identity: Identity = Depends(authenticate)):
    """AI-design and render labelled examples matched to the user's benchmark.

    The orchestration model proposes document kind, typed schema, and ground
    truth; a deterministic renderer draws the images (engine/dataset_gen.py).
    Returns the dataset id plus a preview the console shows for approval. This
    is the explicit path, for a user who wants to see and approve the data
    first; a measured run that starts without data builds its own the same way.
    """
    dataset_id = _generate_dataset(payload.prompt, payload.n, identity)
    return {"dataset_id": dataset_id, "preview": _dataset_preview(dataset_id, identity)}


def _dataset_preview(dataset_id: str, identity: Identity, rows_limit: int = 6) -> dict:
    """What a dataset IS: kind, schema, sample rows, and its document ids."""
    dataset = _dataset_or_404(dataset_id, identity)
    detail = datasets.describe(dataset_id, identity.tenant_id) or {}
    root = os.path.realpath(dataset.path)

    manifest = {}
    manifest_path = os.path.join(root, "manifest.json")
    if os.path.isfile(manifest_path):
        try:
            with open(manifest_path, encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                manifest = loaded
        except (OSError, ValueError):
            manifest = {}

    schema = _dataset_schema(root) or []
    rows: list[dict] = []
    doc_ids: list[str] = []
    truth = os.path.join(root, "ground_truth.csv")
    if os.path.isfile(truth):
        try:
            with open(truth, encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    doc_id = str(row.get("doc_id") or "")
                    if doc_id:
                        doc_ids.append(doc_id)
                    if len(rows) < rows_limit:
                        rows.append({key: str(value or "")[:200] for key, value in row.items()})
        except (OSError, csv.Error):
            pass
    return {
        "dataset_id": dataset_id,
        "kind": detail.get("kind") or "upload",
        "title": manifest.get("title") or "",
        "description": manifest.get("description") or "",
        "document_kind": manifest.get("document_kind") or "",
        "schema": schema,
        "rows": rows,
        "doc_ids": doc_ids,
        "image_count": detail.get("image_count", len(doc_ids)),
    }


@app.get("/api/datasets/{dataset_id}/preview")
def api_dataset_preview(dataset_id: str, identity: Identity = Depends(authenticate)):
    return _dataset_preview(dataset_id, identity)


@app.get("/api/datasets/{dataset_id}/images/{doc_id}")
def api_dataset_image(dataset_id: str, doc_id: str,
                      identity: Identity = Depends(authenticate)):
    """One document image, for the dataset preview. Path-confined to the dataset."""
    dataset = _dataset_or_404(dataset_id, identity)
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", doc_id) or ".." in doc_id:
        raise HTTPException(status_code=422, detail="invalid document id")
    image_dir = os.path.realpath(os.path.join(dataset.path, "images"))
    for suffix in (".png", ".jpg", ".jpeg", ".webp"):
        candidate_path = os.path.realpath(os.path.join(image_dir, doc_id + suffix))
        if os.path.commonpath((image_dir, candidate_path)) != image_dir:
            raise HTTPException(status_code=422, detail="invalid document id")
        if os.path.isfile(candidate_path):
            media = {".png": "image/png", ".jpg": "image/jpeg",
                     ".jpeg": "image/jpeg", ".webp": "image/webp"}[suffix]
            return FileResponse(candidate_path, media_type=media)
    raise HTTPException(status_code=404, detail="document image not found")


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
            # A measured benchmark that reached here without data is not an
            # error to hand back: intake already decided this question is
            # settled by running the candidates, and the spec carries the
            # schema to build examples for. Build them, then run.
            if not (requested and requested.source == "generate"):
                raise HTTPException(status_code=422, detail="a server-issued dataset_id is required")
            dataset_id = _generate_dataset(
                _dataset_design_prompt(session, payload.spec), GENERATED_RUN_DOCS,
                identity, fields=_spec_field_schema(payload.spec))
            generated = _dataset_or_404(dataset_id, identity)
            runs.bind_dataset(session_id, identity.tenant_id, dataset_id, generated.path)
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
                        # Same reason as _fail_worker: the message points the
                        # operator at logs, so the traceback has to reach them.
                        LOGGER.exception(json.dumps({
                            "event": "run_failed", "session_id": session_id,
                            "run_id": run_id, "error": type(exc).__name__}))
                        runs.emit(session_id, "error", {
                            "message": f"Operation failed ({type(exc).__name__}). Check server logs and retry."
                        }, run_id)
            finally:
                runs.finish_run(session_id, cancelled=runs.is_cancelled(session_id), failed=failed,
                                emit_done=True, run_id=run_id)

    threading.Thread(target=worker, daemon=True).start()
    return {"session_id": session_id, "run_id": run_id, "status": "started"}


if WEB_ROOT:
    _web_root = Path(WEB_ROOT).resolve()
    _web_index = _web_root / "index.html"
    if not _web_index.is_file():
        raise RuntimeError("PROOFBENCH_WEB_ROOT must contain index.html")

    @app.get("/{full_path:path}", include_in_schema=False)
    def api_web_app(full_path: str):
        """Serve the built React app in the single-service Railway image."""
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="not found")
        candidate = (_web_root / full_path).resolve()
        if os.path.commonpath((_web_root, candidate)) != str(_web_root):
            raise HTTPException(status_code=404, detail="not found")
        if candidate.is_file():
            headers = ({"Cache-Control": "public, max-age=31536000, immutable"}
                       if full_path.startswith("assets/") else {"Cache-Control": "no-cache"})
            return FileResponse(candidate, headers=headers)
        return FileResponse(_web_index, headers={"Cache-Control": "no-cache"})
