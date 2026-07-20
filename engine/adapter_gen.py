"""Generate candidate adapters and load built-in fallbacks."""

from __future__ import annotations

import importlib
import json
import os
import re
from typing import Any

from engine.candidates.base import Candidate, RESULT_JSON_WRAPPER
from engine.llm_clients import deepseek_client, deepseek_model


FALLBACK_MODULES = {
    "tesseract": "engine.candidates.fallbacks.tesseract",
    "easyocr": "engine.candidates.fallbacks.easyocr",
    "nosana_vlm": "engine.candidates.fallbacks.nosana_vlm",
    "doubleword": "engine.candidates.fallbacks.doubleword",
    "openai_vision": "engine.candidates.fallbacks.openai_vision",
}


def _extract_json_object(content: str) -> dict[str, Any]:
    text = str(content or "").strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("adapter generator response did not contain a JSON object")
    try:
        value = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"adapter generator returned invalid JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise ValueError("adapter generator response must be a JSON object")
    return value


def _validated_payload(value: dict[str, Any]) -> tuple[str, list[str], str, int]:
    display_name = value.get("display_name")
    build_commands = value.get("build_commands")
    adapter_code = value.get("adapter_code")
    setup_complexity = value.get("setup_complexity")
    if not isinstance(display_name, str) or not display_name.strip():
        raise ValueError("adapter generator response has an invalid display_name")
    if not isinstance(build_commands, list) or not all(
        isinstance(command, str) and command.strip() for command in build_commands
    ):
        raise ValueError("adapter generator response has invalid build_commands")
    if not isinstance(adapter_code, str) or "def extract(" not in adapter_code:
        raise ValueError("adapter generator response has invalid adapter_code")
    adapter_code = adapter_code.rstrip()
    if not adapter_code.endswith(RESULT_JSON_WRAPPER):
        adapter_code += "\n\n" + RESULT_JSON_WRAPPER
    if isinstance(setup_complexity, bool) or not isinstance(setup_complexity, int):
        raise ValueError("adapter generator response has an invalid setup_complexity")
    if not 1 <= setup_complexity <= 5:
        raise ValueError("setup_complexity must be between 1 and 5")
    return display_name.strip(), build_commands, adapter_code, setup_complexity


def _guess_kind(docs_md: str) -> str:
    text = docs_md.casefold()
    hosted_markers = (
        "api key",
        "api endpoint",
        "authorization header",
        "bearer token",
        "base_url",
        "base url",
        "hosted api",
        "rest api",
    )
    return "hosted_api" if any(marker in text for marker in hosted_markers) else "local_tool"


def generate_adapter(tool_name: str, docs_md: str, model: str | None = None, env: dict | None = None) -> Candidate:
    """Generate and validate a Candidate with DeepSeek V4 Pro."""
    selected_model = model or deepseek_model(env)

    prompt = f"""Build a Python extraction adapter for {tool_name!r} from the documentation below.
Return STRICT JSON only with this exact shape:
{{"display_name":"...","build_commands":["..."],"adapter_code":"...","setup_complexity":N}}

The adapter_code must define extract(image_path: str) -> dict and return exactly the string
fields invoice_number, date, vendor, and total, using an empty string for missing fields.
It must emit no output itself and must end byte-for-byte with this wrapper:

{RESULT_JSON_WRAPPER}

setup_complexity must be an integer from 1 through 5. Do not include markdown fences.

Documentation:
{docs_md}
"""
    client = deepseek_client(env)
    response = client.chat.completions.create(
        model=selected_model,
        messages=[
            {
                "role": "system",
                "content": "Return one strict JSON object for a safe, documented Python adapter.",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content
    if not isinstance(content, str):
        raise ValueError("adapter generator returned no text content")
    display_name, build_commands, adapter_code, setup_complexity = _validated_payload(
        _extract_json_object(content)
    )
    return Candidate(
        name=tool_name,
        display_name=display_name,
        docs_url="",
        kind=_guess_kind(docs_md),
        build_commands=build_commands,
        adapter_code=adapter_code,
        setup_complexity=setup_complexity,
    )


def repair_adapter(adapter_code: str, error_output: str, model: str | None = None, env: dict | None = None) -> str:
    """Repair a generated adapter with DeepSeek and return validated source code."""
    selected_model = model or deepseek_model(env)
    prompt = f"""Repair the Python adapter below using the runtime error.
Return STRICT JSON only as {{"adapter_code":"..."}}.
The code must define extract(image_path: str) -> dict, return exactly the string fields
invoice_number, date, vendor, and total, and end byte-for-byte with this wrapper:

{RESULT_JSON_WRAPPER}

ADAPTER:
{adapter_code}

ERROR:
{error_output}
"""
    response = deepseek_client(env).chat.completions.create(
        model=selected_model,
        messages=[
            {"role": "system", "content": "Repair Python integrations. Return strict JSON."},
            {"role": "user", "content": prompt},
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content
    value = _extract_json_object(content or "")
    repaired = value.get("adapter_code")
    if not isinstance(repaired, str) or "def extract(" not in repaired:
        raise ValueError("adapter repair returned invalid source code")
    repaired = repaired.rstrip()
    if not repaired.endswith(RESULT_JSON_WRAPPER):
        repaired += "\n\n" + RESULT_JSON_WRAPPER
    return repaired


def get_fallback(name: str) -> Candidate | None:
    """Load a built-in candidate lazily by name."""
    module_name = FALLBACK_MODULES.get(str(name).strip().casefold())
    if module_name is None:
        return None
    module = importlib.import_module(module_name)
    return module.candidate()
