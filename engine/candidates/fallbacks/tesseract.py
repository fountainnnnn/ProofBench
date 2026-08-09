"""Built-in Tesseract OCR candidate."""

from __future__ import annotations

from engine.candidates.base import (
    Candidate,
    RESULT_JSON_WRAPPER,
    SCHEMA_LOADER,
    TEXT_FIELDS_EXTRACTOR,
)

# Field parsing lives in the shared, schema-driven TEXT_FIELDS_EXTRACTOR; this
# body contributes only what is Tesseract's own — reading text off the image.
_ADAPTER_BODY = r'''
import pytesseract
from PIL import Image


def extract(image_path: str) -> dict:
    text = pytesseract.image_to_string(Image.open(image_path))
    return _extract_fields(text)
'''


def candidate() -> Candidate:
    """Return the Tesseract fallback candidate."""
    return Candidate(
        name="tesseract",
        display_name="Tesseract OCR 5.x",
        docs_url="https://tesseract-ocr.github.io/tessdoc/",
        kind="local_tool",
        build_commands=[
            "apt-get update && apt-get install -y tesseract-ocr python3-pip",
            "pip install pytesseract pillow",
        ],
        adapter_code="\n\n".join((
            SCHEMA_LOADER, TEXT_FIELDS_EXTRACTOR,
            _ADAPTER_BODY.strip(), RESULT_JSON_WRAPPER,
        )),
        setup_complexity=2,
    )
