"""A session's name should say what it is about, not repeat its opening line.

Titles were the first forty characters of the first message, so the sidebar
filled with truncated questions — "what service should I use for a RAG cha..."
— that were hard to tell apart at a glance and told you nothing the message
itself did not. One small completion over the opening exchange names the
subject instead.

Titling is cosmetic: every failure path here returns the old truncation rather
than raising, because a chat must never break over its own label.
"""
from __future__ import annotations

MAX_TITLE_CHARS = 48
MAX_TITLE_WORDS = 8

# Enough of each side to know the topic. The assistant's opening paragraph
# names the candidates; sending the whole turn would just cost tokens.
_EXCERPT_CHARS = 600

# Absurd for six words, and deliberately so: the orchestration models reason
# before they answer and that thinking is billed against the same budget. At 24
# tokens deepseek-v4-flash spent the lot thinking and returned empty content; at
# 256 it still ran out on roughly one call in three (measured 124-225 tokens of
# reasoning for the same prompt). A ceiling only costs what is generated, and
# generation stops at the title, so headroom here is free and truncation is not.
_MAX_TOKENS = 1024

TITLE_SYSTEM = (
    "You name benchmarking conversations. Reply with the title only: 2 to 6 "
    "words, Title Case, naming the subject being compared and the task — for "
    "example 'RAG Platforms for SharePoint' or 'Invoice OCR Accuracy'. Never "
    "answer the question, never add quotes, punctuation, or any preamble."
)


def _flat(text) -> str:
    return " ".join(str(text or "").split())


def fallback_title(message: str) -> str:
    """The original rule, kept as the floor under every failure."""
    text = _flat(message)
    if not text:
        return "New benchmark"
    return (text[:40] + "...") if len(text) > 40 else text


def _clean(raw: str) -> str:
    """Take a title out of model output, or nothing at all.

    Small models like to answer instead of naming, so anything sentence-shaped
    is rejected rather than truncated into a misleading fragment.
    """
    line = _flat(raw).strip("\"'“”‘’ ").rstrip(".:;,!?")
    if not line:
        return ""
    words = line.split(" ")
    if len(words) > MAX_TITLE_WORDS or len(line) > MAX_TITLE_CHARS:
        return ""
    return line


def _default_complete(env, **kwargs):
    from engine.agent import _orchestrator_complete

    return _orchestrator_complete(env, **kwargs)


def _conversation(message: str, reply: str) -> str:
    parts = [f"User: {_flat(message)[:_EXCERPT_CHARS]}"]
    body = _flat(reply)[:_EXCERPT_CHARS]
    if body:
        parts.append(f"Assistant: {body}")
    return "\n".join(parts)


def summarize_title(message: str, reply: str = "", *, env=None, complete=None) -> str:
    """Name the conversation, falling back to its opening line."""
    complete = complete or _default_complete
    try:
        response = complete(
            env,
            messages=[
                {"role": "system", "content": TITLE_SYSTEM},
                {"role": "user", "content": _conversation(message, reply)},
            ],
            max_tokens=_MAX_TOKENS,
            temperature=0.2,
        )
        title = _clean(response.choices[0].message.content)
    except Exception:
        title = ""
    return title or fallback_title(message)
