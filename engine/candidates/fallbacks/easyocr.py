"""Built-in EasyOCR candidate."""

from __future__ import annotations

from engine.candidates.base import (
    Candidate,
    RESULT_JSON_WRAPPER,
    SCHEMA_LOADER,
    TEXT_FIELDS_EXTRACTOR,
)

# Field parsing lives in the shared, schema-driven TEXT_FIELDS_EXTRACTOR; this
# body contributes only what is EasyOCR's own — reading text off the image.
_ADAPTER_BODY = r'''
import easyocr


_READER = None


def _reader():
    global _READER
    if _READER is None:
        # The sandbox carries a GPU; fall back to CPU only if it is absent.
        try:
            import torch

            _use_gpu = bool(torch.cuda.is_available())
        except Exception:
            _use_gpu = False
        _READER = easyocr.Reader(["en"], gpu=_use_gpu)
    return _READER


def extract(image_path: str) -> dict:
    fragments = _reader().readtext(image_path, detail=0, paragraph=False)
    return _extract_fields("\n".join(str(fragment) for fragment in fragments))
'''


def candidate() -> Candidate:
    """Return the CPU-only EasyOCR fallback candidate."""
    return Candidate(
        name="easyocr",
        display_name="EasyOCR (CPU)",
        docs_url="https://github.com/JaidedAI/EasyOCR",
        kind="local_tool",
        # PyPI's default Torch resolution can pull multi-gigabyte CUDA wheels
        # into a CPU-only sandbox. Install the official CPU wheels first so
        # EasyOCR reuses them instead of exhausting the ephemeral environment.
        build_commands=[
            # CUDA wheels, pinned to an index so the resolver cannot wander: the
            # sandbox carries a GPU and EasyOCR is 21x faster on it (measured
            # 12.5s per document on 4 CPUs against 0.60s on an RTX-4090).
            ("python -m pip install --index-url "
             "https://download.pytorch.org/whl/cu124 torch torchvision"),
            "python -m pip install easyocr opencv-python-headless",
            # EasyOCR fetches its detection and recognition weights on first
            # use, not at install time. Without this the download happens inside
            # the first inference of every process, which for a per-image
            # candidate meant paying it once per document (measured ~34s each).
            "python -c \"import easyocr; easyocr.Reader(['en'], gpu=False)\"",
        ],
        adapter_code="\n\n".join((
            SCHEMA_LOADER, TEXT_FIELDS_EXTRACTOR,
            _ADAPTER_BODY.strip(), RESULT_JSON_WRAPPER,
        )),
        setup_complexity=3,
    )
