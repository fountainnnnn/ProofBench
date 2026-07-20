"""Central LLM client configuration for code generation and batch workloads."""

from __future__ import annotations

import asyncio
import os
from typing import Any


DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DOUBLEWORD_BASE_URL = "https://api.doubleword.ai/v1"


def deepseek_client(env: dict | None = None):
    """Return the synchronous DeepSeek client used for code generation."""
    from openai import OpenAI

    env = env or os.environ
    api_key = env.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is required for code generation")
    return OpenAI(
        api_key=api_key,
        base_url=env.get("DEEPSEEK_BASE_URL", DEEPSEEK_BASE_URL),
    )


def deepseek_model(env: dict | None = None) -> str:
    return (env or os.environ).get("DEEPSEEK_MODEL", "deepseek-v4-flash")


def doubleword_batch_client(env: dict | None = None):
    """Return a Doubleword-backed drop-in replacement for AsyncOpenAI."""
    from autobatcher import BatchOpenAI

    env = env or os.environ
    api_key = env.get("DOUBLEWORD_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("DOUBLEWORD_API_KEY is required for batch processing")
    return BatchOpenAI(
        api_key=api_key,
        base_url=env.get("DOUBLEWORD_BASE_URL", DOUBLEWORD_BASE_URL),
        batch_size=int(env.get("DOUBLEWORD_BATCH_SIZE", "500")),
        batch_window_seconds=float(
            env.get("DOUBLEWORD_BATCH_WINDOW_SECONDS", "5")
        ),
        poll_interval_seconds=float(
            env.get("DOUBLEWORD_POLL_INTERVAL_SECONDS", "5")
        ),
        completion_window=env.get("DOUBLEWORD_COMPLETION_WINDOW", "1h"),
    )


async def batch_chat_completions(
    requests: list[dict[str, Any]],
    model: str | None = None,
    env: dict | None = None,
) -> list[Any]:
    """Submit independent chat-completion requests as one Doubleword batch."""
    env = env or os.environ
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
