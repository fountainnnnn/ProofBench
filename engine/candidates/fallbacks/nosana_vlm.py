"""Built-in Nosana hosted VLM candidate."""

from __future__ import annotations

from engine.candidates.base import Candidate, RESULT_JSON_WRAPPER
from engine.candidates.fallbacks._http_security import SECURE_OPENAI_TRANSPORT


_ADAPTER_BODY = SECURE_OPENAI_TRANSPORT + r'''
import base64
import json
import os
from pathlib import Path

from openai import OpenAI


FIELDS = ("invoice_number", "date", "vendor", "total")


def _json_object(content: str) -> dict:
    start = content.find("{")
    end = content.rfind("}")
    if start < 0 or end < start:
        raise ValueError("model reply did not contain a JSON object")
    value = json.loads(content[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("model reply JSON was not an object")
    return {field: "" if value.get(field) is None else str(value.get(field, "")) for field in FIELDS}


def extract(image_path: str) -> dict:
    encoded = base64.b64encode(Path(image_path).read_bytes()).decode("ascii")
    suffix = Path(image_path).suffix.casefold()
    media_type = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(suffix)
    if media_type is None:
        raise ValueError(f"unsupported image format: {suffix or '<none>'}")
    base_url, http_client = _secure_openai_transport(
        os.environ["NOSANA_BASE_URL"]
    )
    client = OpenAI(
        api_key=os.environ["NOSANA_API_KEY"],
        base_url=base_url,
        http_client=http_client,
        max_retries=0,
    )
    response = client.chat.completions.create(
        model=os.environ["NOSANA_MODEL"],
        messages=[
            {
                "role": "system",
                "content": "Extract invoice fields. Return STRICT JSON only with exactly invoice_number, date, vendor, and total as string fields. Use an empty string when missing.",
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Extract the four invoice fields from this image."},
                    {"type": "image_url", "image_url": {"url": "data:" + media_type + ";base64," + encoded}},
                ],
            },
        ],
        temperature=0,
    )
    content = response.choices[0].message.content
    if not isinstance(content, str):
        raise ValueError("model reply did not contain text")
    return _json_object(content)
'''


def candidate() -> Candidate:
    """Return the Nosana VLM fallback candidate."""
    return Candidate(
        name="nosana_vlm",
        display_name="Nosana Hosted VLM",
        docs_url="https://docs.nosana.com",
        kind="hosted_api",
        build_commands=["pip install openai pillow"],
        adapter_code=_ADAPTER_BODY.strip() + "\n\n" + RESULT_JSON_WRAPPER,
        setup_complexity=2,
    )
