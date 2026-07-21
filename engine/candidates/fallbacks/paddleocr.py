"""Built-in PaddleOCR candidate using the supported 3.x pipeline API."""

from __future__ import annotations

from engine.candidates.base import Candidate, RESULT_JSON_WRAPPER


_ADAPTER_BODY = r'''
import os
import re
from contextlib import redirect_stderr, redirect_stdout

# Avoid a network source probe on every short-lived benchmark invocation. Model
# assets are still fetched by PaddleOCR itself when they are not cached yet.
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", "BOS")
os.environ.setdefault("GLOG_minloglevel", "2")

from paddleocr import PaddleOCR

_PIPELINE = None


def _pipeline():
    global _PIPELINE
    if _PIPELINE is None:
        with open(os.devnull, "w") as sink, redirect_stdout(sink), redirect_stderr(sink):
            _PIPELINE = PaddleOCR(
                lang="en",
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
            )
    return _PIPELINE


def _extract_fields(text: str) -> dict:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    invoice_number = ""
    invoice_patterns = (
        r"\b(?:invoice\s*(?:number|no\.?|#)?|inv(?:oice)?\s*(?:number|no\.?|#)?)\s*[:#-]?\s*([A-Z]{0,6}[-/]?\d{3,})\b",
        r"\b(INV[-/]?\d{3,})\b",
    )
    for pattern in invoice_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            invoice_number = match.group(1).strip()
            break

    date_value = ""
    date_patterns = (
        r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b",
        r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",
        r"\b\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{4}\b",
    )
    for pattern in date_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            date_value = match.group(0).strip()
            break

    total = ""
    amount_pattern = re.compile(r"(?:SGD\s*|\$\s*)?-?\d[\d,]*(?:\.\d{1,2})?", re.IGNORECASE)
    total_lines = [
        line for line in lines
        if re.search(r"\b(?:grand\s+total|total|amount\s+due)\b", line, re.IGNORECASE)
        and not re.search(r"\bsubtotal\b", line, re.IGNORECASE)
    ]
    for line in reversed(total_lines):
        amounts = amount_pattern.findall(line)
        if amounts:
            total = amounts[-1].strip()
            break

    return {
        "invoice_number": invoice_number,
        "date": date_value,
        "vendor": lines[0] if lines else "",
        "total": total,
    }


def extract(image_path: str) -> dict:
    pipeline = _pipeline()
    with open(os.devnull, "w") as sink, redirect_stdout(sink), redirect_stderr(sink):
        results = pipeline.predict(image_path)
    fragments = []
    for result in results:
        payload = result.json
        if callable(payload):
            payload = payload()
        if isinstance(payload, dict):
            data = payload.get("res", payload)
            if isinstance(data, dict):
                fragments.extend(str(item) for item in data.get("rec_texts", []) if item)
    return _extract_fields("\n".join(fragments))
'''


def candidate() -> Candidate:
    """Return the CPU PaddleOCR 3.x candidate."""
    return Candidate(
        name="paddleocr",
        display_name="PaddleOCR 3.x (CPU)",
        docs_url="https://www.paddleocr.ai/latest/en/version3.x/pipeline_usage/OCR.html",
        kind="local_tool",
        build_commands=[
            "apt-get update && apt-get install -y libgl1 libglib2.0-0 libgomp1",
            "python -m pip install paddlepaddle==3.2.0",
            "python -m pip install paddleocr",
        ],
        adapter_code=_ADAPTER_BODY.strip() + "\n\n" + RESULT_JSON_WRAPPER,
        setup_complexity=3,
        batch_safe=False,
    )
