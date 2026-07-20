"""Built-in EasyOCR candidate."""

from __future__ import annotations

from engine.candidates.base import Candidate, RESULT_JSON_WRAPPER


_ADAPTER_BODY = r'''
import re

import easyocr


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
    reader = easyocr.Reader(["en"], gpu=False)
    fragments = reader.readtext(image_path, detail=0, paragraph=False)
    return _extract_fields("\n".join(str(fragment) for fragment in fragments))
'''


def candidate() -> Candidate:
    """Return the CPU-only EasyOCR fallback candidate."""
    return Candidate(
        name="easyocr",
        display_name="EasyOCR (CPU)",
        docs_url="https://github.com/JaidedAI/EasyOCR",
        kind="local_tool",
        build_commands=["pip install easyocr opencv-python-headless"],
        adapter_code=_ADAPTER_BODY.strip() + "\n\n" + RESULT_JSON_WRAPPER,
        setup_complexity=3,
    )
