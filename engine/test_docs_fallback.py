"""Bounds and URL policy for the direct documentation retrieval fallback.

These cover the path taken when Oxylabs is unconfigured or failing. The fallback
fetches pages from this process, so every bound it claims is asserted here.
"""

from __future__ import annotations

import socket

import pytest

from engine import docs_intel
from engine.agent import Orchestrator
from engine.candidates.base import Candidate

# The chain only tries providers holding credentials, so these tests supply them.
OX = {"OXYLABS_USERNAME": "u", "OXYLABS_PASSWORD": "p"}


def public_resolver(host, port, **_kwargs):
    """Controlled resolver: every name resolves to one public address."""
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


class FakeResponse:
    def __init__(self, status_code=200, headers=None, chunks=(b"",)):
        self.status_code = status_code
        self.headers = {key.lower(): value for key, value in (headers or {}).items()}
        self._chunks = chunks

    def iter_bytes(self):
        yield from self._chunks

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class FakeClient:
    """Stands in for the client secure_httpx_client returns for one hop."""

    def __init__(self, response, log):
        self._response = response
        self._log = log
        self.closed = False

    def stream(self, method, url, timeout=None, headers=None):
        self._log.append((method, url, timeout, headers))
        return self._response

    def close(self):
        self.closed = True


def install(monkeypatch, responses):
    """Serve one queued response per hop; record the client built for each."""
    from engine import network_security

    log: list = []
    hops: list = []
    queue = list(responses)

    def fake_secure(base_url, allowed_hosts=None):
        client = FakeClient(queue.pop(0), log)
        hops.append((base_url, allowed_hosts, client))
        return base_url, client

    monkeypatch.setattr(network_security, "secure_httpx_client", fake_secure)
    return log, hops


def fetch(url):
    return docs_intel.fetch_documentation(url, resolver=public_resolver)


@pytest.fixture(autouse=True)
def _no_local_fallback(monkeypatch):
    """These tests exercise the paid-provider chain and its direct-fetch tail, so
    the always-on local fallback is off by default. It has dedicated coverage in
    test_scraper_chain.py where it is turned on deliberately."""
    from engine import selfhosted

    monkeypatch.delenv("PROOFBENCH_INSECURE_DEV", raising=False)
    selfhosted.reset_cache()


@pytest.fixture
def offline_dns(monkeypatch):
    """Apply the real URL policy against the controlled resolver, not live DNS."""
    from engine import network_security

    real = network_security.validate_external_url
    monkeypatch.setattr(
        network_security, "validate_external_url",
        lambda url, **kwargs: real(url, **{"resolver": public_resolver, **kwargs}),
    )


def test_direct_fetch_returns_bounded_readable_text(monkeypatch):
    body = (b"<html><head><style>a{color:red}</style></head><body>"
            b"<h1>Alpha  SDK</h1><script>evil()</script>"
            b"<p>Install with pip.</p></body></html>")
    log, hops = install(
        monkeypatch,
        [FakeResponse(headers={"content-type": "text/html; charset=utf-8"}, chunks=[body])],
    )

    text = fetch("https://docs.example.com/alpha")

    assert "Alpha SDK" in text
    assert "Install with pip." in text
    # Script and style bodies are never presented as documentation.
    assert "evil()" not in text
    assert "color:red" not in text
    assert len(text) <= docs_intel.DIRECT_FETCH_MAX_TEXT_CHARS
    assert log[0][0] == "GET"
    assert log[0][2] <= docs_intel.DIRECT_FETCH_TIMEOUT_SECONDS
    assert hops[0][2].closed is True


def test_direct_fetch_revalidates_every_redirect_hop(monkeypatch):
    """Each hop builds its own pinned client, so approval is never inherited."""
    _log, hops = install(
        monkeypatch,
        [
            FakeResponse(302, {"location": "https://cdn.example.org/alpha"}),
            FakeResponse(headers={"content-type": "text/plain"}, chunks=[b"Alpha docs"]),
        ],
    )

    assert fetch("https://docs.example.com/alpha") == "Alpha docs"
    assert [hop[0] for hop in hops] == [
        "https://docs.example.com/alpha",
        "https://cdn.example.org/alpha",
    ]
    assert all(hop[2].closed for hop in hops)


@pytest.mark.parametrize(
    "url",
    [
        "http://docs.example.com/alpha",            # http is not fetched directly
        "ftp://docs.example.com/alpha",
        "https://localhost/alpha",
        "https://127.0.0.1/alpha",
        "https://[::1]/alpha",
        "https://169.254.169.254/latest/meta-data",
        "https://metadata.google.internal/alpha",
        "https://10.0.0.5/alpha",
        "https://user:password@docs.example.com/alpha",
    ],
)
def test_direct_fetch_refuses_unsafe_targets(monkeypatch, url):
    install(monkeypatch, [])
    with pytest.raises(ValueError, match="external URL is not permitted"):
        fetch(url)


def test_direct_fetch_refuses_a_redirect_into_a_private_address(monkeypatch):
    install(monkeypatch, [FakeResponse(302, {"location": "https://169.254.169.254/meta"})])
    with pytest.raises(ValueError, match="external URL is not permitted"):
        fetch("https://docs.example.com/alpha")


def test_direct_fetch_refuses_a_redirect_without_a_location(monkeypatch):
    install(monkeypatch, [FakeResponse(302, {})])
    with pytest.raises(RuntimeError, match="did not supply a location"):
        fetch("https://docs.example.com/alpha")


def test_direct_fetch_bounds_the_redirect_chain(monkeypatch):
    hops = docs_intel.DIRECT_FETCH_MAX_REDIRECTS + 1
    install(
        monkeypatch,
        [FakeResponse(302, {"location": f"https://hop{index}.example.com/a"})
         for index in range(hops)],
    )
    with pytest.raises(RuntimeError, match="redirect budget"):
        fetch("https://docs.example.com/alpha")


def test_direct_fetch_truncates_an_oversized_body(monkeypatch):
    oversized = b"x" * (docs_intel.DIRECT_FETCH_MAX_BYTES * 2)
    install(
        monkeypatch,
        [FakeResponse(headers={"content-type": "text/plain"}, chunks=[oversized])],
    )
    assert len(fetch("https://docs.example.com/alpha")) <= docs_intel.DIRECT_FETCH_MAX_TEXT_CHARS


def test_direct_fetch_rejects_an_oversized_declared_length(monkeypatch):
    install(
        monkeypatch,
        [FakeResponse(headers={
            "content-type": "text/plain",
            "content-length": str(docs_intel.DIRECT_FETCH_MAX_BYTES * 2),
        }, chunks=[b"ignored"])],
    )
    with pytest.raises(RuntimeError, match="size budget"):
        fetch("https://docs.example.com/alpha")


def test_direct_fetch_refuses_unsupported_content_types(monkeypatch):
    install(
        monkeypatch,
        [FakeResponse(headers={"content-type": "application/octet-stream"},
                      chunks=[b"\x00\x01"])],
    )
    with pytest.raises(RuntimeError, match="unsupported content type"):
        fetch("https://docs.example.com/alpha")


def test_direct_fetch_refuses_a_non_200_response(monkeypatch):
    install(monkeypatch, [FakeResponse(503, {"content-type": "text/plain"})])
    with pytest.raises(RuntimeError, match="HTTP 503"):
        fetch("https://docs.example.com/alpha")


def test_scrape_page_falls_back_to_a_direct_fetch_when_oxylabs_fails(monkeypatch, offline_dns):
    """Losing Oxylabs degrades to a bounded direct fetch, not to a hard failure."""
    def oxylabs_down(_payload, _env):
        raise RuntimeError("Oxylabs credentials are missing")

    monkeypatch.setattr(docs_intel, "_query", oxylabs_down)
    monkeypatch.setattr(docs_intel, "fetch_documentation", lambda url: f"docs for {url}")

    assert docs_intel.scrape_page("https://docs.example.com/alpha") == (
        "docs for https://docs.example.com/alpha"
    )


def test_scrape_page_prefers_oxylabs_when_it_succeeds(monkeypatch, offline_dns):
    monkeypatch.setattr(
        docs_intel, "_query",
        lambda _payload, _env: {"results": [{"content": "oxylabs content"}]},
    )
    monkeypatch.setattr(
        docs_intel, "fetch_documentation",
        lambda _url: pytest.fail("the direct fallback must not run when Oxylabs works"),
    )
    assert docs_intel.scrape_page("https://docs.example.com/alpha", OX) == "oxylabs content"


def test_scrape_page_reports_both_failures_without_echoing_provider_text(monkeypatch, offline_dns):
    secret = "oxylabs-secret-value"

    def oxylabs_down(_payload, _env):
        raise RuntimeError(f"auth rejected for {secret}")

    def direct_down(_url):
        raise RuntimeError(f"direct fetch saw {secret}")

    monkeypatch.setattr(docs_intel, "_query", oxylabs_down)
    monkeypatch.setattr(docs_intel, "fetch_documentation", direct_down)

    with pytest.raises(RuntimeError) as raised:
        docs_intel.scrape_page("https://docs.example.com/alpha", OX)

    assert secret not in str(raised.value)
    # Error TYPES only. The chain names "providers" rather than one vendor now
    # that three can answer, but the property under test is unchanged: no
    # provider's error text reaches the caller.
    assert "providers RuntimeError" in str(raised.value)
    assert "direct RuntimeError" in str(raised.value)


def test_openrouter_credentials_can_never_be_sandbox_entitled(tmp_path):
    """OpenRouter is orchestration only: no candidate may ever receive its key."""
    from engine.builtin_adapters import SANDBOX_ELIGIBLE_CREDENTIALS
    from engine.tools import env_prelude

    assert not any(name.startswith("OPENROUTER_") for name in SANDBOX_ELIGIBLE_CREDENTIALS)

    orchestrator = Orchestrator(
        "openrouter-boundary", str(tmp_path), lambda _event, _data: None,
        provider_env={
            "OPENROUTER_API_KEY": "orchestration-secret",
            "OPENROUTER_BASE_URL": "https://openrouter.ai/api/v1",
            "OPENROUTER_MODEL": "openai/gpt-4o-mini",
        },
    )
    for forbidden in ("OPENROUTER_API_KEY", "OPENROUTER_BASE_URL", "OPENROUTER_MODEL"):
        with pytest.raises(ValueError, match="orchestration credentials"):
            orchestrator.register_trusted_candidate(
                Candidate("greedy", "Greedy", "", "hosted_api", [], "pass"),
                [forbidden],
            )

    # A candidate that names the variable outright still receives nothing.
    code = 'import os\nprint(os.environ["OPENROUTER_API_KEY"])'
    injected = env_prelude(code, orchestrator.ctx.env_passthrough, frozenset())
    assert injected == code
    assert "orchestration-secret" not in injected
