"""Central LLM provider configuration for orchestration, assessment, and reports.

Provider selection is capability based. Each capability names the providers that
can serve it, in preference order; the first one this deployment has configured
wins. That is what lets a deployment that has only OpenRouter perform every
required LLM task without OpenAI, DeepSeek, or Doubleword being present.

Nothing here is ever entitled inside a candidate sandbox. Every name below is
covered by ``engine.agent.NEVER_SANDBOX_PREFIXES``.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any


DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DOUBLEWORD_BASE_URL = "https://api.doubleword.ai/v1"
MOONSHOT_BASE_URL = "https://api.moonshot.ai/v1"
OPENAI_BASE_URL = "https://api.openai.com/v1"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Bounded fan-out for providers without a native batch API. Assessment batches
# are small (one request per candidate) and this keeps a wide candidate list
# from opening an unbounded number of concurrent provider connections.
MAX_CONCURRENT_COMPLETIONS = 8


@dataclass(frozen=True)
class ProviderSpec:
    """One OpenAI-compatible provider and the exact env names it reads."""

    api_key_env: str
    default_base_url: str
    default_model: str
    model_env: str
    base_url_env: str | None = None
    # None means "deployment-configurable URL": secure_httpx_client locks the
    # transport to the hostname of the already-allowlisted base URL. A literal
    # set pins a server-owned constant host.
    allowed_hosts: frozenset[str] | None = None


PROVIDERS: dict[str, ProviderSpec] = {
    "moonshot": ProviderSpec(
        api_key_env="MOONSHOT_API_KEY",
        default_base_url=MOONSHOT_BASE_URL,
        default_model="kimi-k2-thinking",
        model_env="KIMI_MODEL",
        allowed_hosts=frozenset({"api.moonshot.ai"}),
    ),
    "openai": ProviderSpec(
        api_key_env="OPENAI_API_KEY",
        default_base_url=OPENAI_BASE_URL,
        default_model="gpt-4o",
        model_env="OPENAI_ORCHESTRATOR_MODEL",
        allowed_hosts=frozenset({"api.openai.com"}),
    ),
    "openrouter": ProviderSpec(
        api_key_env="OPENROUTER_API_KEY",
        default_base_url=OPENROUTER_BASE_URL,
        default_model="openai/gpt-4o-mini",
        model_env="OPENROUTER_MODEL",
        base_url_env="OPENROUTER_BASE_URL",
    ),
    "deepseek": ProviderSpec(
        api_key_env="DEEPSEEK_API_KEY",
        default_base_url=DEEPSEEK_BASE_URL,
        default_model="deepseek-v4-flash",
        model_env="DEEPSEEK_MODEL",
        base_url_env="DEEPSEEK_BASE_URL",
    ),
    "doubleword": ProviderSpec(
        api_key_env="DOUBLEWORD_API_KEY",
        default_base_url=DOUBLEWORD_BASE_URL,
        default_model="deepseek-ai/DeepSeek-V4-Pro",
        model_env="DOUBLEWORD_MODEL",
        base_url_env="DOUBLEWORD_BASE_URL",
    ),
}

# capability -> providers in preference order. The first configured one is used;
# callers that tolerate a failure walk the rest of the list.
CAPABILITY_PROVIDERS: dict[str, tuple[str, ...]] = {
    "orchestration": ("moonshot", "openai", "openrouter", "deepseek"),
    "assessment": ("doubleword", "openrouter", "openai", "deepseek"),
    "report": ("moonshot", "openai", "openrouter", "deepseek"),
    "codegen": ("deepseek", "openrouter"),
}

# Capabilities whose provider is pinned by ORCHESTRATOR_PROVIDER. The historical
# variable named one of kimi/moonshot/openai; openrouter is accepted too.
_ORCHESTRATOR_PINNED = ("orchestration", "report")
_PIN_ALIASES = {"kimi": "moonshot", "moonshot": "moonshot",
                "openai": "openai", "openrouter": "openrouter"}


def _env(env: dict | None):
    return os.environ if env is None else env


def _value(env, name: str) -> str:
    return str((env.get(name) if name else "") or "").strip()


def provider_configured(provider: str, env: dict | None = None) -> bool:
    """True when this deployment supplied the provider's API key."""
    spec = PROVIDERS.get(provider)
    return bool(spec) and bool(_value(_env(env), spec.api_key_env))


def _pinned_provider(capability: str, env) -> str | None:
    if capability not in _ORCHESTRATOR_PINNED:
        return None
    return _PIN_ALIASES.get(_value(env, "ORCHESTRATOR_PROVIDER").casefold())


def capability_providers(capability: str, env: dict | None = None) -> tuple[str, ...]:
    """Configured providers for a capability, most preferred first.

    An explicit pin moves that provider to the front but does not delete the
    rest: a pinned provider that fails at request time still falls back rather
    than failing the whole run.
    """
    env = _env(env)
    order = CAPABILITY_PROVIDERS[capability]
    pin = _pinned_provider(capability, env)
    if pin and pin in order:
        order = (pin, *(item for item in order if item != pin))
    return tuple(item for item in order if provider_configured(item, env))


def resolve_provider(capability: str, env: dict | None = None) -> str:
    """The provider that will serve this capability, or raise if none is set."""
    found = capability_providers(capability, env)
    if not found:
        names = ", ".join(
            PROVIDERS[item].api_key_env for item in CAPABILITY_PROVIDERS[capability]
        )
        raise RuntimeError(f"no provider is configured for {capability}; set one of: {names}")
    return found[0]


def provider_model(provider: str, env: dict | None = None) -> str:
    spec = PROVIDERS[provider]
    return _value(_env(env), spec.model_env) or spec.default_model


def provider_base_url(provider: str, env: dict | None = None) -> str:
    spec = PROVIDERS[provider]
    return _value(_env(env), spec.base_url_env) or spec.default_base_url


def _client_kwargs(provider: str, env, secure_factory):
    spec = PROVIDERS[provider]
    env = _env(env)
    api_key = _value(env, spec.api_key_env)
    if not api_key:
        raise RuntimeError(f"{spec.api_key_env} is required for provider {provider!r}")
    base_url, http_client = secure_factory(
        provider_base_url(provider, env), spec.allowed_hosts
    )
    return {"api_key": api_key, "base_url": base_url,
            "http_client": http_client, "max_retries": 0}


def chat_client(provider: str, env: dict | None = None):
    """Synchronous OpenAI-compatible client on the hardened transport."""
    from openai import OpenAI

    from engine.network_security import secure_httpx_client

    return OpenAI(**_client_kwargs(provider, env, secure_httpx_client))


def async_chat_client(provider: str, env: dict | None = None):
    """Asynchronous OpenAI-compatible client on the hardened transport."""
    from openai import AsyncOpenAI

    from engine.network_security import secure_async_httpx_client

    return AsyncOpenAI(**_client_kwargs(provider, env, secure_async_httpx_client))


def openrouter_client(env: dict | None = None):
    """Return the synchronous OpenRouter client."""
    return chat_client("openrouter", env)


def openrouter_model(env: dict | None = None) -> str:
    return provider_model("openrouter", env)


def deepseek_client(env: dict | None = None):
    """Return the synchronous DeepSeek client used for code generation."""
    env = dict(env or {})
    if not _value(env, "DEEPSEEK_API_KEY"):
        raise RuntimeError("DEEPSEEK_API_KEY is required for code generation")
    return chat_client("deepseek", env)


def deepseek_model(env: dict | None = None) -> str:
    return dict(env or {}).get("DEEPSEEK_MODEL", "deepseek-v4-flash")


def codegen_client(env: dict | None = None) -> tuple[Any, str]:
    """Return (client, model) for adapter generation and repair.

    DeepSeek stays the preferred code model; OpenRouter serves the capability
    when DeepSeek is not configured.
    """
    env = dict(env or {})
    provider = resolve_provider("codegen", env)
    return chat_client(provider, env), provider_model(provider, env)


def doubleword_batch_client(env: dict | None = None):
    """Return a Doubleword-backed drop-in replacement for AsyncOpenAI."""
    from autobatcher import BatchOpenAI

    from engine.network_security import secure_async_httpx_client

    env = dict(env or {})
    api_key = env.get("DOUBLEWORD_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("DOUBLEWORD_API_KEY is required for batch processing")
    base_url, http_client = secure_async_httpx_client(
        env.get("DOUBLEWORD_BASE_URL", DOUBLEWORD_BASE_URL)
    )
    client = BatchOpenAI(
        api_key=api_key,
        base_url=base_url,
        http_client=http_client,
        max_retries=0,
        batch_size=int(env.get("DOUBLEWORD_BATCH_SIZE", "500")),
        batch_window_seconds=float(
            env.get("DOUBLEWORD_BATCH_WINDOW_SECONDS", "5")
        ),
        poll_interval_seconds=float(
            env.get("DOUBLEWORD_POLL_INTERVAL_SECONDS", "5")
        ),
        completion_window=env.get("DOUBLEWORD_COMPLETION_WINDOW", "1h"),
    )
    # autobatcher 0.10 creates an additional raw AsyncClient for partial
    # result polling. Replace it because its constructor does not expose a
    # transport hook and otherwise honors process proxy variables.
    displaced = getattr(client, "_http_client", None)
    _, secure_raw_client = secure_async_httpx_client(base_url)
    client._http_client = secure_raw_client
    close_displaced = getattr(displaced, "aclose", None)
    if callable(close_displaced) and displaced is not http_client:
        try:
            asyncio.get_running_loop().create_task(close_displaced())
        except RuntimeError:
            asyncio.run(close_displaced())
    return client


async def batch_chat_completions(
    requests: list[dict[str, Any]],
    model: str | None = None,
    env: dict | None = None,
) -> list[Any]:
    """Submit independent chat-completion requests as one Doubleword batch."""
    env = dict(env or {})
    selected_model = model or env.get(
        "DOUBLEWORD_MODEL", "deepseek-ai/DeepSeek-V4-Pro"
    )
    client = doubleword_batch_client(env)
    try:
        tasks = [
            client.chat.completions.create(model=selected_model, **request)
            for request in requests
        ]
        return await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        await client.close()


async def _concurrent_chat_completions(
    provider: str,
    requests: list[dict[str, Any]],
    model: str,
    env: dict | None = None,
) -> list[Any]:
    """Run requests concurrently against one OpenAI-compatible provider."""
    client = async_chat_client(provider, env)
    limit = asyncio.Semaphore(MAX_CONCURRENT_COMPLETIONS)

    async def one(request: dict[str, Any]):
        async with limit:
            return await client.chat.completions.create(model=model, **request)

    try:
        return await asyncio.gather(
            *(one(request) for request in requests), return_exceptions=True
        )
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            await close()


async def provider_chat_completions(
    provider: str,
    requests: list[dict[str, Any]],
    model: str | None = None,
    env: dict | None = None,
) -> list[Any]:
    """Run one assessment workload on a named provider.

    Doubleword gets its native autobatcher; every other OpenAI-compatible
    provider gets bounded concurrent completions. Both return one entry per
    request, either a response or the exception that request raised.
    """
    env = dict(env or {})
    selected_model = model or provider_model(provider, env)
    if provider == "doubleword":
        return await batch_chat_completions(requests, model=selected_model, env=env)
    return await _concurrent_chat_completions(provider, requests, selected_model, env)
