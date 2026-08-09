"""Candidate contract for ProofBench (CONTRACTS §1).

Every benchmark candidate — local OCR tool, hosted VLM, or anything the agent
discovers — is described by the same dataclass. The runner is fully generic.
"""

from dataclasses import dataclass


@dataclass
class Candidate:
    name: str                    # unique slug, e.g. "tesseract"
    display_name: str            # "Tesseract OCR 5.x"
    docs_url: str                # documentation the integration was built from
    kind: str                    # "local_tool" | "hosted_api"
    build_commands: list[str]    # shell cmds to install/configure INSIDE a Daytona sandbox
    adapter_code: str            # python source executed INSIDE the sandbox (see below)
    setup_complexity: int = 1    # 1 (trivial) .. 5 (painful); agent may worsen it on repairs
    pricing_url: str = ""        # where pricing was scraped from ("" if free/local)


# adapter_code contract (executed inside the sandbox, CWD contains the dataset):
#   - defines extract(image_path: str) -> dict whose keys are the run's declared
#     schema (empty string for missing fields). The schema is uploaded into the
#     sandbox as pb_schema.json ([{"name": str, "type": str}, ...]); adapters
#     that need it at runtime embed SCHEMA_LOADER below. When the file is absent
#     the loader answers with the original invoice schema, so an adapter built
#     from this contract behaves identically on legacy runs.
#   - MUST end with exactly this wrapper (no other prints):
RESULT_JSON_WRAPPER = '''
import json, sys, time
_t0 = time.time()
try:
    _out = extract(sys.argv[1])
    print("RESULT_JSON:" + json.dumps({"ok": True, "fields": _out, "latency_s": round(time.time() - _t0, 3)}))
except Exception as e:
    print("RESULT_JSON:" + json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}))
'''.strip()

# Embedded verbatim at the top of adapter sources that read the run's schema at
# runtime. Kept as one shared snippet so every first-party adapter answers the
# same way when pb_schema.json is absent.
SCHEMA_LOADER = '''
import json as _pb_json
import os as _pb_os


def _pb_schema():
    """The run's declared fields as [{"name", "type"}, ...]."""
    default = [{"name": "invoice_number", "type": "text"},
               {"name": "date", "type": "date"},
               {"name": "vendor", "type": "text"},
               {"name": "total", "type": "currency"}]
    try:
        if _pb_os.path.isfile("pb_schema.json"):
            loaded = _pb_json.load(open("pb_schema.json", encoding="utf-8"))
            fields = [{"name": str(f.get("name") or ""), "type": str(f.get("type") or "text")}
                      for f in loaded if isinstance(f, dict) and f.get("name")]
            if fields:
                return fields
    except Exception:
        pass
    return default
'''.strip()

# Shared, schema-driven field extraction over OCR text. Embedded (after
# SCHEMA_LOADER) by every OCR-based first-party adapter, so the three engines
# differ only in how they read text off the image. For the legacy invoice
# columns it reproduces the exact heuristics those adapters always used; any
# other field is found generically — first by its own label in the text, then
# by a pattern its declared type implies.
TEXT_FIELDS_EXTRACTOR = r'''
import re as _pb_re

_PB_DATE_PATTERNS = (
    r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b",
    r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",
    r"\b\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{4}\b",
)
_PB_AMOUNT = r"(?:[A-Z]{3}\s*|\$\s*|€\s*|£\s*)?-?\d[\d,]*(?:\.\d{1,2})?"


def _pb_labeled_value(text, name):
    """The value written after this field's own label, e.g. "PO Ref: 8841"."""
    words = [w for w in name.replace("_", " ").split() if w]
    if not words:
        return ""
    label = r"\s*".join(_pb_re.escape(w) for w in words)
    match = _pb_re.search(
        r"\b" + label + r"\s*(?:number|no\.?|#)?\s*[:#=-]\s*(.{1,80})",
        text, flags=_pb_re.IGNORECASE)
    if not match:
        return ""
    value = match.group(1).strip()
    return _pb_re.split(r"\s{2,}|\t", value)[0].strip()


def _pb_typed_value(text, lines, field_type):
    if field_type == "date":
        for pattern in _PB_DATE_PATTERNS:
            match = _pb_re.search(pattern, text, flags=_pb_re.IGNORECASE)
            if match:
                return match.group(0).strip()
        return ""
    if field_type in ("currency", "number"):
        amounts = _pb_re.findall(_PB_AMOUNT, text)
        return amounts[-1].strip() if amounts else ""
    return ""


def _pb_invoice_number(text):
    for pattern in (
        r"\b(?:invoice\s*(?:number|no\.?|#)?|inv(?:oice)?\s*(?:number|no\.?|#)?)\s*[:#-]?\s*([A-Z]{0,6}[-/]?\d{3,})\b",
        r"\b(INV[-/]?\d{3,})\b",
    ):
        match = _pb_re.search(pattern, text, flags=_pb_re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


def _pb_total(text, lines):
    total_lines = [
        line for line in lines
        if _pb_re.search(r"\b(?:grand\s+total|total|amount\s+due)\b", line, _pb_re.IGNORECASE)
        and not _pb_re.search(r"\bsubtotal\b", line, _pb_re.IGNORECASE)
    ]
    for line in reversed(total_lines):
        amounts = _pb_re.findall(_PB_AMOUNT, line)
        if amounts:
            return amounts[-1].strip()
    return ""


def _extract_fields(text):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    out = {}
    for field in _pb_schema():
        name, ftype = field["name"], field.get("type", "text")
        if name == "invoice_number":
            out[name] = _pb_invoice_number(text)
        elif name == "vendor":
            out[name] = lines[0] if lines else ""
        elif name == "total":
            out[name] = _pb_total(text, lines)
        else:
            value = _pb_labeled_value(text, name)
            if not value and name == "date":
                value = _pb_typed_value(text, lines, "date")
            out[name] = value or _pb_typed_value(text, lines, ftype)
    return out
'''.strip()
