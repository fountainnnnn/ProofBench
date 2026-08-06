"""Outbound URL policy shared by engine HTTP and LLM clients."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urlsplit


_BLOCKED_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
    "metadata.google.internal.",
}


def _public_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def validate_external_url(
    url: str,
    *,
    require_https: bool = True,
    resolver: Callable | None = socket.getaddrinfo,
    allowed_hosts: frozenset[str] | set[str] | None = None,
) -> str:
    """Validate an outbound URL and optionally resolve every address publicly.

    Production integrations may pass ``socket.getaddrinfo`` (or an equivalent
    controlled resolver) to defend against hostnames resolving to private
    networks. Literal IPs and known metadata/localhost names are always denied.
    """
    try:
        parsed = urlsplit(str(url))
        hostname = (parsed.hostname or "").casefold().rstrip(".")
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise ValueError("external URL is not permitted") from exc
    schemes = {"https"} if require_https else {"http", "https"}
    normalized_allowed_hosts = (
        frozenset(str(host).casefold().rstrip(".") for host in allowed_hosts)
        if allowed_hosts is not None
        else None
    )
    if (
        parsed.scheme.casefold() not in schemes
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or hostname in _BLOCKED_HOSTS
        or hostname.endswith(".localhost")
        or (
            normalized_allowed_hosts is not None
            and hostname not in normalized_allowed_hosts
        )
        or (require_https and port not in (None, 443))
    ):
        raise ValueError("external URL is not permitted")
    try:
        literal = ipaddress.ip_address(hostname.split("%", 1)[0])
    except ValueError:
        literal = None
    if literal is not None and not _public_address(hostname):
        raise ValueError("external URL is not permitted")
    if resolver is not None and literal is None:
        try:
            default_port = 443 if parsed.scheme.casefold() == "https" else 80
            answers = resolver(
                hostname, port or default_port, type=socket.SOCK_STREAM
            )
        except Exception as exc:
            raise ValueError("external URL could not be validated") from exc
        addresses = {str(answer[4][0]) for answer in answers if answer[4]}
        if not addresses or any(not _public_address(address) for address in addresses):
            raise ValueError("external URL is not permitted")
    return str(url)


class OutboundURLPolicy:
    """Fail-closed policy installed on every OpenAI-compatible transport.

    Provider hostnames are server-owned constants, redirects and environment
    proxies are disabled by the client factories below, and this hook resolves
    the destination again immediately before every request. This prevents a
    configurable attacker hostname from entering the transport and catches a
    public-to-private DNS change before a retry or subsequent request.
    """

    def __init__(self, allowed_hosts, resolver: Callable = socket.getaddrinfo):
        self.allowed_hosts = frozenset(
            str(host).casefold().rstrip(".") for host in allowed_hosts
        )
        self.resolver = resolver

    def resolve_addresses(self, host: str, port: int) -> tuple[str, ...]:
        """Resolve and approve the exact addresses used by the socket backend."""
        normalized = str(host).casefold().rstrip(".")
        if normalized not in self.allowed_hosts or port != 443:
            raise ValueError("external URL is not permitted")
        try:
            literal = ipaddress.ip_address(normalized.split("%", 1)[0])
        except ValueError:
            literal = None
        if literal is not None:
            if not _public_address(normalized):
                raise ValueError("external URL is not permitted")
            return (normalized,)
        try:
            answers = self.resolver(normalized, port, type=socket.SOCK_STREAM)
        except Exception as exc:
            raise ValueError("external URL could not be validated") from exc
        addresses = tuple(dict.fromkeys(
            str(answer[4][0]) for answer in answers if answer[4]
        ))
        if not addresses or any(not _public_address(address) for address in addresses):
            raise ValueError("external URL is not permitted")
        return addresses

    def validate(self, url) -> str:
        return validate_external_url(
            str(url),
            resolver=self.resolver,
            allowed_hosts=self.allowed_hosts,
        )

    def request_hook(self, request) -> None:
        self.validate(request.url)

    async def async_request_hook(self, request) -> None:
        self.validate(request.url)


class _PinnedSyncBackend:
    """httpcore backend that connects to the already-approved IP, preserving TLS SNI."""

    def __init__(self, policy: OutboundURLPolicy):
        from httpcore._backends.sync import SyncBackend

        self.policy = policy
        self.backend = SyncBackend()

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        last_error = None
        for address in self.policy.resolve_addresses(host, port):
            try:
                return self.backend.connect_tcp(
                    address, port, timeout=timeout, local_address=local_address,
                    socket_options=socket_options,
                )
            except Exception as exc:  # pragma: no cover - failover depends on network state
                last_error = exc
        if last_error is not None:
            raise last_error
        raise ValueError("external URL is not permitted")

    def connect_unix_socket(self, *_args, **_kwargs):
        raise ValueError("Unix sockets are not permitted for external providers")


class _PinnedAsyncBackend:
    def __init__(self, policy: OutboundURLPolicy):
        from httpcore._backends.auto import AutoBackend

        self.policy = policy
        self.backend = AutoBackend()

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        last_error = None
        for address in self.policy.resolve_addresses(host, port):
            try:
                return await self.backend.connect_tcp(
                    address, port, timeout=timeout, local_address=local_address,
                    socket_options=socket_options,
                )
            except Exception as exc:  # pragma: no cover - failover depends on network state
                last_error = exc
        if last_error is not None:
            raise last_error
        raise ValueError("external URL is not permitted")

    async def connect_unix_socket(self, *_args, **_kwargs):
        raise ValueError("Unix sockets are not permitted for external providers")

    async def sleep(self, seconds):
        await self.backend.sleep(seconds)


def _sync_transport(policy: OutboundURLPolicy):
    import httpcore
    import httpx

    transport = httpx.HTTPTransport(trust_env=False, retries=0)
    transport._pool = httpcore.ConnectionPool(
        ssl_context=httpx.create_ssl_context(trust_env=False),
        retries=0,
        network_backend=_PinnedSyncBackend(policy),
    )
    return transport


def _async_transport(policy: OutboundURLPolicy):
    import httpcore
    import httpx

    transport = httpx.AsyncHTTPTransport(trust_env=False, retries=0)
    transport._pool = httpcore.AsyncConnectionPool(
        ssl_context=httpx.create_ssl_context(trust_env=False),
        retries=0,
        network_backend=_PinnedAsyncBackend(policy),
    )
    return transport


def _transport_hosts(base_url: str, allowed_hosts) -> frozenset[str]:
    """Lock a prevalidated configurable URL to its initial hostname."""
    if allowed_hosts is not None:
        return frozenset(allowed_hosts)
    try:
        hostname = (urlsplit(str(base_url)).hostname or "").casefold().rstrip(".")
    except (TypeError, ValueError) as exc:
        raise ValueError("external URL is not permitted") from exc
    if not hostname:
        raise ValueError("external URL is not permitted")
    return frozenset({hostname})


def secure_httpx_client(
    base_url: str, allowed_hosts=None
) -> tuple[str, object]:
    """Return a validated base URL and redirect/proxy-free synchronous client.

    ``allowed_hosts=None`` is for deployment-configurable provider URLs that
    have already passed the server allowlist. Their initial hostname is locked
    here and revalidated by the transport hook before every request.
    """
    import httpx

    policy = OutboundURLPolicy(_transport_hosts(base_url, allowed_hosts))
    safe_base = policy.validate(base_url)
    client = httpx.Client(
        follow_redirects=False,
        trust_env=False,
        transport=_sync_transport(policy),
        event_hooks={"request": [policy.request_hook]},
    )
    return safe_base, client


def local_http_enabled() -> bool:
    """Whether a self-hosted local scraper may be reached over plain HTTP.

    Off by default. The hardened client above forbids loopback, private, and
    non-HTTPS destinations so that no attacker-supplied hostname can pivot into
    the internal network. A local operator running SearXNG or Crawl4AI in Docker
    needs exactly that forbidden shape, so this one relaxation is gated behind
    the same insecure-dev flag as runtime credential writes and never applies in
    a deployment that has not opted into it.
    """
    import os

    return os.environ.get("PROOFBENCH_INSECURE_DEV") == "1"


def _is_local_host(hostname: str) -> bool:
    """True only for loopback or private hosts.

    A public hostname is refused here on purpose: this path exists for a local
    tool, so it must never become a second, weaker route to a public target,
    and refusing anything non-local also closes a DNS-rebinding pivot.
    """
    normalized = str(hostname or "").casefold().rstrip(".")
    if normalized in ("localhost", "localhost.localdomain") or normalized.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(normalized.split("%", 1)[0])
    except ValueError:
        return False
    return address.is_loopback or address.is_private


def local_service_listening(base_url: str, timeout: float = 0.15) -> bool:
    """Whether something is accepting connections at a local service's address.

    A TCP connect rather than an HTTP request, for two reasons: "is it running"
    is a transport question, so there is no health path to guess at; and an HTTP
    ping to a closed port costs the full timeout once per resolved address, which
    made `localhost` (both ::1 and 127.0.0.1) take seconds to answer. A refused
    connection returns immediately, so this is cheap enough to call per request.
    """
    if not local_http_enabled():
        return False
    try:
        parsed = urlsplit(str(base_url))
        hostname = parsed.hostname
        if not hostname or not _is_local_host(hostname):
            return False
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except Exception:
        return False
    for family, socket_type, proto, _canon, address in infos:
        try:
            with socket.socket(family, socket_type, proto) as probe:
                probe.settimeout(timeout)
                if probe.connect_ex(address) == 0:
                    return True
        except Exception:
            continue
    return False


def local_service_client(base_url: str) -> tuple[str, object]:
    """A plain client for a self-hosted local service (SearXNG, Crawl4AI).

    Deliberately bypasses the outbound URL policy, so it is fenced in two ways:
    it works only when `local_http_enabled()` is true, and only for a loopback
    or private base URL. Redirects and environment proxies stay disabled, and
    the caller is expected to issue requests only against this local base.
    """
    import httpx

    if not local_http_enabled():
        raise RuntimeError("local service URLs require PROOFBENCH_INSECURE_DEV=1")
    parsed = urlsplit(str(base_url))
    if (parsed.scheme not in ("http", "https") or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or not _is_local_host(parsed.hostname)):
        raise RuntimeError("local service URL must be a loopback or private host")
    client = httpx.Client(follow_redirects=False, trust_env=False)
    return str(base_url).rstrip("/"), client


def secure_async_httpx_client(
    base_url: str, allowed_hosts=None
) -> tuple[str, object]:
    """Return a validated base URL and redirect/proxy-free asynchronous client."""
    import httpx

    policy = OutboundURLPolicy(_transport_hosts(base_url, allowed_hosts))
    safe_base = policy.validate(base_url)
    client = httpx.AsyncClient(
        follow_redirects=False,
        trust_env=False,
        transport=_async_transport(policy),
        event_hooks={"request": [policy.async_request_hook]},
    )
    return safe_base, client
