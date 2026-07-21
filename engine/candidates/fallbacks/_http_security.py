"""Standalone transport guard embedded in hosted fallback adapters."""

from __future__ import annotations


SECURE_OPENAI_TRANSPORT = r'''
import ipaddress
import socket
from urllib.parse import urlsplit

import httpx
import httpcore


def _public_provider_address(value: str) -> bool:
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


def _secure_openai_transport(base_url: str, allowed_hosts=None):
    """Revalidate a provider host immediately before every sandbox request.

    Configurable endpoints have already passed server-side validation. This
    in-sandbox hook closes the redirect/re-resolution gap at the actual client.
    """
    try:
        parsed = urlsplit(str(base_url))
        hostname = (parsed.hostname or "").casefold().rstrip(".")
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise ValueError("provider URL is not permitted") from exc
    allowed = {
        str(host).casefold().rstrip(".")
        for host in (allowed_hosts if allowed_hosts is not None else {hostname})
    }
    if (
        parsed.scheme.casefold() != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or hostname not in allowed
    ):
        raise ValueError("provider URL is not permitted")

    def resolve(request_host, request_port):
        if request_host != hostname or request_port != 443:
            raise ValueError("provider URL is not permitted")
        try:
            answers = socket.getaddrinfo(
                request_host, request_port, type=socket.SOCK_STREAM
            )
        except Exception as exc:
            raise ValueError("provider URL could not be validated") from exc
        addresses = tuple(dict.fromkeys(
            str(answer[4][0]) for answer in answers if answer[4]
        ))
        if not addresses or any(
            not _public_provider_address(address) for address in addresses
        ):
            raise ValueError("provider URL is not permitted")
        return addresses

    def validate(url):
        request_url = urlsplit(str(url))
        request_host = (request_url.hostname or "").casefold().rstrip(".")
        if (
            request_url.scheme.casefold() != "https"
            or request_host != hostname
            or request_url.port not in (None, 443)
        ):
            raise ValueError("provider URL is not permitted")
        resolve(request_host, request_url.port or 443)

    validate(base_url)

    def request_hook(request):
        validate(request.url)

    class PinnedBackend:
        def __init__(self):
            from httpcore._backends.sync import SyncBackend
            self.backend = SyncBackend()

        def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
            last_error = None
            for address in resolve(host.casefold().rstrip("."), port):
                try:
                    return self.backend.connect_tcp(
                        address, port, timeout=timeout, local_address=local_address,
                        socket_options=socket_options,
                    )
                except Exception as exc:
                    last_error = exc
            if last_error is not None:
                raise last_error
            raise ValueError("provider URL is not permitted")

        def connect_unix_socket(self, *_args, **_kwargs):
            raise ValueError("Unix sockets are not permitted")

    transport = httpx.HTTPTransport(trust_env=False, retries=0)
    transport._pool = httpcore.ConnectionPool(
        ssl_context=httpx.create_ssl_context(trust_env=False),
        retries=0,
        network_backend=PinnedBackend(),
    )

    return str(base_url), httpx.Client(
        follow_redirects=False,
        trust_env=False,
        transport=transport,
        event_hooks={"request": [request_hook]},
    )
'''
