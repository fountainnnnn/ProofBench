"""Built-in PaddleOCR candidate using the supported 3.x pipeline API."""

from __future__ import annotations

from engine.candidates.base import (
    Candidate,
    RESULT_JSON_WRAPPER,
    SCHEMA_LOADER,
    TEXT_FIELDS_EXTRACTOR,
)

# Field parsing lives in the shared, schema-driven TEXT_FIELDS_EXTRACTOR; this
# body contributes only what is PaddleOCR's own — reading text off the image.
_ADAPTER_BODY = r'''
import os
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
            # Same reason as EasyOCR: PaddleOCR downloads its pipeline models on
            # first predict(), so the fetch is done once here rather than inside
            # the first inference of every run.
            ("python -c \"from paddleocr import PaddleOCR; "
             "PaddleOCR(lang='en', use_doc_orientation_classify=False, "
             "use_doc_unwarping=False, use_textline_orientation=False)\""),
        ],
        adapter_code="\n\n".join((
            SCHEMA_LOADER, TEXT_FIELDS_EXTRACTOR,
            _ADAPTER_BODY.strip(), RESULT_JSON_WRAPPER,
        )),
        setup_complexity=3,
    )
