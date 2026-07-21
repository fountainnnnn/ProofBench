"""Authentication and tenant-scoped secret storage for the ProofBench API."""
from __future__ import annotations

import hmac
import json
import os
import re
import threading
from dataclasses import dataclass

from fastapi import HTTPException, Request


MIN_TOKEN_LENGTH = 32
_REJECTED_EXAMPLE_TOKENS = {
    "replace-with-at-least-32-random-characters",
    "0123456789abcdef0123456789abcdef",
}


@dataclass(frozen=True)
class Identity:
    tenant_id: str


def local_mode() -> bool:
    """True when the operator explicitly opted into the tokenless local profile.

    `PROOFBENCH_INSECURE_DEV=1` is the single, explicit bypass. It is supported
    only for a loopback-bound single-operator deployment; exposing that listener
    beyond 127.0.0.1 requires opting back into `PROOFBENCH_API_KEYS`.
    """
    return os.environ.get("PROOFBENCH_INSECURE_DEV") == "1"


def _raw_api_keys() -> str:
    return os.environ.get("PROOFBENCH_API_KEYS", "").strip()


def check_auth_mode() -> None:
    """Raise unless exactly one authentication mode is configured.

    The two modes mean opposite things, so a deployment that sets both has not
    expressed an intent this process is allowed to guess at. Resolving the
    ambiguity either way is a security decision: preferring the tokenless local
    profile silently disables the tokens the operator went to the trouble of
    configuring, and preferring the tokens silently contradicts an explicit
    `PROOFBENCH_INSECURE_DEV=1`. So mixed configuration is refused outright,
    and so is the empty configuration that authenticates nothing by accident.
    """
    insecure_dev = local_mode()
    keys = _raw_api_keys()
    if insecure_dev and keys:
        raise RuntimeError(
            "PROOFBENCH_INSECURE_DEV=1 and PROOFBENCH_API_KEYS are mutually exclusive; "
            "set exactly one authentication mode"
        )
    if not insecure_dev and not keys:
        raise RuntimeError(
            "no authentication mode is configured; set PROOFBENCH_API_KEYS, or "
            "PROOFBENCH_INSECURE_DEV=1 for the loopback-only tokenless profile"
        )


def auth_mode() -> str:
    """The deployment's authentication mode, as reported to the browser."""
    check_auth_mode()
    return "local" if local_mode() else "authenticated"


def _configured_keys() -> dict[str, str]:
    raw = os.environ.get("PROOFBENCH_API_KEYS", "")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("PROOFBENCH_API_KEYS must be a JSON object") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("PROOFBENCH_API_KEYS must be a JSON object")
    keys: dict[str, str] = {}
    for tenant, token in parsed.items():
        if not isinstance(tenant, str) or not isinstance(token, str):
            raise RuntimeError("PROOFBENCH_API_KEYS tenants and tokens must be strings")
        tenant = tenant.strip()
        token = token.strip()
        if not tenant or len(tenant) > 128 or not token:
            raise RuntimeError("PROOFBENCH_API_KEYS contains an invalid tenant or token")
        # Token strength is not conditional on the mode: configuring keys at all
        # means this deployment authenticates, and `check_auth_mode` has already
        # rejected the mixed configuration that used to make this exemption
        # reachable.
        if len(token) < MIN_TOKEN_LENGTH or token.casefold() in _REJECTED_EXAMPLE_TOKENS:
            raise RuntimeError(
                "PROOFBENCH_API_KEYS tokens must be at least 32 characters and must not use examples"
            )
        if tenant in keys or token in keys.values():
            raise RuntimeError("PROOFBENCH_API_KEYS contains duplicate tenants or tokens")
        keys[tenant] = token
    return keys


def auth_is_configured() -> bool:
    try:
        check_auth_mode()
    except RuntimeError:
        return False
    return local_mode() or bool(_configured_keys())


def authenticate_token(token: str) -> Identity:
    # The mode check comes first so a mixed configuration can never be resolved
    # into the tokenless local identity below.
    try:
        check_auth_mode()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="authentication configuration is invalid") from exc
    if local_mode():
        return Identity(os.environ.get("PROOFBENCH_DEV_TENANT", "local-dev"))
    try:
        keys = _configured_keys()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="authentication configuration is invalid") from exc
    if not keys:
        raise HTTPException(status_code=503, detail="authentication is not configured")
    if not token:
        raise HTTPException(status_code=401, detail="authentication required",
                            headers={"WWW-Authenticate": "Bearer"})
    for tenant, expected in keys.items():
        if hmac.compare_digest(token, expected):
            return Identity(tenant)
    raise HTTPException(status_code=401, detail="invalid credentials",
                        headers={"WWW-Authenticate": "Bearer"})


def authenticate(request: Request) -> Identity:
    """Authenticate with Bearer, X-API-Key, or the EventSource-compatible cookie."""
    authorization = request.headers.get("authorization", "")
    token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    token = token or request.headers.get("x-api-key", "").strip()
    cookie_token = request.cookies.get("proofbench_api_key", "").strip()
    if not token and cookie_token and request.method not in {"GET", "HEAD"}:
        raise HTTPException(status_code=403, detail="cookie authentication is read-only; use an API key header")
    token = token or cookie_token
    return authenticate_token(token)


class TenantCredentialStore:
    """Process-local credential vault partitioned by authenticated tenant."""

    def __init__(self) -> None:
        self._values: dict[str, dict[str, str]] = {}
        self._lock = threading.RLock()

    def names(self, tenant_id: str) -> list[str]:
        with self._lock:
            return sorted(self._values.get(tenant_id, {}))

    def snapshot(self, tenant_id: str) -> dict[str, str]:
        with self._lock:
            return dict(self._values.get(tenant_id, {}))

    def set(self, tenant_id: str, name: str, value: str) -> None:
        with self._lock:
            self._values.setdefault(tenant_id, {})[name] = value

    def delete(self, tenant_id: str, name: str) -> None:
        with self._lock:
            values = self._values.get(tenant_id)
            if values:
                values.pop(name, None)
                if not values:
                    self._values.pop(tenant_id, None)


provider_credentials = TenantCredentialStore()

_SENSITIVE_NAME = re.compile(
    r"^(?:authorization|cookie|password|secret|token|api[_-]?key)$|"
    r"(?:password|secret|api[_-]?key|access_token|refresh_token)$", re.I
)
_SENSITIVE_ENV = re.compile(r"(?:KEYS?|TOKEN|PASSWORD|SECRET)", re.I)
_INLINE_SECRET = re.compile(
    r"(?i)(\b(?:authorization|cookie|password|secret|token|api[_-]?key)\b\s*[:=]\s*)([^\s,;]+)"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")


def redact_event_data(data: dict, tenant_id: str) -> dict:
    """Remove credential-shaped values before SSE data is stored or emitted."""
    secrets = set(provider_credentials.snapshot(tenant_id).values())
    try:
        secrets.update(_configured_keys().values())
    except RuntimeError:
        pass
    for name, value in os.environ.items():
        if value and _SENSITIVE_ENV.search(name):
            secrets.add(value)

    def redact(value, key: str = ""):
        if _SENSITIVE_NAME.search(key):
            return "[REDACTED]"
        if isinstance(value, dict):
            return {str(child_key): redact(child, str(child_key))
                    for child_key, child in value.items()}
        if isinstance(value, list):
            return [redact(child) for child in value]
        if isinstance(value, tuple):
            return [redact(child) for child in value]
        if isinstance(value, str):
            result = _BEARER.sub("Bearer [REDACTED]", value)
            result = _INLINE_SECRET.sub(r"\1[REDACTED]", result)
            for secret in secrets:
                if len(secret) >= 6:
                    result = result.replace(secret, "[REDACTED]")
            return result
        return value

    return redact(data)
