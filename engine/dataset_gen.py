"""AI-proposed synthetic datasets, rendered deterministically.

When a benchmark has no labelled dataset, ProofBench can make one that matches
what the user is actually trying to test: the orchestration model proposes a
document kind, a typed field schema, and the ground-truth rows; a deterministic
Pillow renderer then draws one document image per row. The model authors
*content* only — every pixel is drawn by fixed code from the validated rows, so
the ground truth is exact by construction, not by a second model reading images.

Nothing about the proposal is trusted: names, types, row counts, and value sizes
are all validated here, and the renderer draws only validated strings. The
result is written as a normal dataset directory (images/ + ground_truth.csv)
plus schema.json (the typed schema) and manifest.json (what this dataset is,
shown in the console's preview).
"""

from __future__ import annotations

import csv
import hashlib
import json
import random
import re
from pathlib import Path

from engine.fields import FIELD_TYPES, MAX_FIELDS, infer_type

MIN_DOCS = 5
MAX_DOCS = 30
DEFAULT_DOCS = 12
MAX_VALUE_CHARS = 80
MAX_TITLE_CHARS = 80
MAX_DESCRIPTION_CHARS = 400
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

_PROPOSAL_SYSTEM = (
    "You design labelled benchmark datasets. Given what a user wants to test, "
    "return STRICT JSON only — no markdown fences, no prose — with exactly this "
    "shape:\n"
    '{"title": str, "description": str, "document_kind": str,'
    ' "fields": [{"name": str, "type": "text"|"date"|"currency"|"number"}],'
    ' "rows": [{<field name>: str, ...}]}\n'
    "Rules: field names are lowercase snake_case; 2 to 10 fields; every row has "
    "a realistic string value for every field; dates in ISO YYYY-MM-DD; amounts "
    "as plain decimals without symbols; values vary across rows the way real "
    "documents vary (different vendors, dates, references, amounts). The "
    "documents will be rendered as images and tools will be benchmarked on "
    "extracting these exact values, so values must be short (under 80 chars), "
    "printable, and unambiguous."
)


def _clean_text(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def propose_dataset(prompt: str, n: int = DEFAULT_DOCS, env: dict | None = None,
                    fields: list | None = None) -> dict:
    """Ask the orchestration model for a dataset proposal, validated.

    Returns {"title", "description", "document_kind", "fields", "rows"} where
    fields is [{"name", "type"}, ...] and rows is a list of n dicts of strings.
    Raises ValueError when no valid proposal can be obtained.

    ``fields`` pins the schema. A benchmark spec that reached here already
    declares the columns its candidates will be scored on, and the evaluator
    compares the spec's columns against the ground truth by name — so the model
    is told to write rows for exactly those columns, and a proposal that renames,
    drops, or invents one is rejected rather than quietly rendered.
    """
    from engine.agent import _orchestrator_complete

    n = max(MIN_DOCS, min(MAX_DOCS, int(n or DEFAULT_DOCS)))
    required = _required_fields(fields)
    request = (
        f"The user wants to benchmark: {prompt.strip()[:2000]}\n"
        f"Design a labelled dataset of exactly {n} documents for it."
    )
    if required:
        request += (
            "\nThe benchmark already declares its schema. Use EXACTLY these fields, "
            "with these names and types, and no others:\n"
            + json.dumps(required)
        )
    response = _orchestrator_complete(
        dict(env or {}),
        messages=[{"role": "system", "content": _PROPOSAL_SYSTEM},
                  {"role": "user", "content": request}],
        temperature=0.4,
    )
    content = response.choices[0].message.content or ""
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end < start:
        raise ValueError("dataset proposal contained no JSON object")
    proposal = json.loads(content[start:end + 1])
    if not isinstance(proposal, dict):
        raise ValueError("dataset proposal was not a JSON object")
    return _validated_proposal(proposal, n, required)


def _required_fields(fields: list | None) -> list[dict] | None:
    """Normalize a caller-pinned schema, or None when the model may choose."""
    if not fields:
        return None
    required: list[dict] = []
    for item in fields:
        name = str(item.get("name") or "") if isinstance(item, dict) else str(item or "")
        if not _NAME_RE.fullmatch(name):
            raise ValueError(f"invalid required field name: {name!r}")
        if any(f["name"] == name for f in required):
            raise ValueError(f"duplicate required field: {name!r}")
        declared = item.get("type") if isinstance(item, dict) else None
        required.append({"name": name,
                         "type": declared if declared in FIELD_TYPES else infer_type(name)})
    if not (2 <= len(required) <= min(10, MAX_FIELDS)):
        raise ValueError("a pinned schema must declare 2 to 10 fields")
    return required


def _validated_proposal(proposal: dict, n: int,
                        required: list[dict] | None = None) -> dict:
    if required:
        # The schema is the caller's, not the model's. Taking it wholesale means
        # a model that echoed the field list back with one name misspelled
        # cannot produce a dataset the evaluator would score as all-missing.
        fields = [dict(field) for field in required]
        names = {field["name"] for field in fields}
        proposed = proposal.get("fields")
        if isinstance(proposed, list):
            offered = {str((item or {}).get("name") or "")
                       for item in proposed if isinstance(item, dict)}
            if offered and offered != names:
                raise ValueError("proposal did not use the required field schema")
        return _finished_proposal(proposal, fields, n)

    raw_fields = proposal.get("fields")
    if not isinstance(raw_fields, list) or not (2 <= len(raw_fields) <= min(10, MAX_FIELDS)):
        raise ValueError("proposal must declare 2 to 10 fields")
    fields: list[dict] = []
    for item in raw_fields:
        name = str((item or {}).get("name") or "") if isinstance(item, dict) else ""
        if not _NAME_RE.fullmatch(name):
            raise ValueError(f"invalid proposed field name: {name!r}")
        if any(f["name"] == name for f in fields):
            raise ValueError(f"duplicate proposed field: {name!r}")
        declared = item.get("type") if isinstance(item, dict) else None
        fields.append({"name": name,
                       "type": declared if declared in FIELD_TYPES else infer_type(name)})
    return _finished_proposal(proposal, fields, n)


def _finished_proposal(proposal: dict, fields: list[dict], n: int) -> dict:
    """Rows and metadata, once the schema is settled."""
    raw_rows = proposal.get("rows")
    if not isinstance(raw_rows, list) or not raw_rows:
        raise ValueError("proposal contained no rows")
    rows: list[dict] = []
    for raw in raw_rows[:n]:
        if not isinstance(raw, dict):
            continue
        row = {f["name"]: _clean_text(raw.get(f["name"]), MAX_VALUE_CHARS) for f in fields}
        if all(row.values()):
            rows.append(row)
    if len(rows) < MIN_DOCS:
        raise ValueError("proposal contained too few complete rows")

    return {
        "title": _clean_text(proposal.get("title"), MAX_TITLE_CHARS) or "Generated dataset",
        "description": _clean_text(proposal.get("description"), MAX_DESCRIPTION_CHARS),
        "document_kind": _clean_text(proposal.get("document_kind"), MAX_TITLE_CHARS)
        or "document",
        "fields": fields,
        "rows": rows,
    }


# ---------------------------------------------------------------- rendering

def _font(size: int):
    from PIL import ImageFont

    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow before load_default(size=...)
        return ImageFont.load_default()


def _humanize(name: str) -> str:
    return name.replace("_", " ").title()


def _render_document(path: Path, kind: str, row: dict, fields: list[dict],
                     template: int, rng: random.Random) -> None:
    from PIL import Image, ImageDraw

    width, height = 900, 1100
    image = Image.new("RGB", (width, height), "#f8f9fb" if template == 1 else "white")
    draw = ImageDraw.Draw(image)
    title_font, heading, body = _font(40), _font(24), _font(21)

    accent = ("#253c78", "#1f5f4a", "#6b2d40")[template % 3]
    draw.text((64, 48), _humanize(kind).upper()[:40], font=title_font, fill=accent)
    draw.line((64, 112, width - 64, 112), fill="#9aa9bd", width=2)

    # Field lines: label and value, positions varying by template so a layout
    # heuristic cannot succeed by memorising one arrangement.
    y = 170 if template != 2 else 210
    step = 64
    order = list(fields)
    if template == 2:
        order = list(reversed(order))
    for field in order:
        label = _humanize(field["name"])
        value = row[field["name"]]
        if template == 1:
            draw.text((64, y), f"{label}:", font=heading, fill="#526070")
            draw.text((360, y), value, font=body, fill="#182230")
        else:
            draw.text((64, y), f"{label}: {value}", font=body, fill="#182230")
        y += step

    # A footer block so documents are not minimal label sheets.
    draw.rectangle((64, height - 160, width - 64, height - 112), fill="#e8edf6")
    draw.text((80, height - 148), "Generated by ProofBench - synthetic data",
              font=_font(17), fill="#526070")
    # Light deterministic noise so OCR sees texture, not flat vectors.
    for _ in range(180):
        x, ny = rng.randrange(0, width), rng.randrange(0, height)
        image.putpixel((x, ny), (235, 238, 242))
    image.save(path)


def render_dataset(proposal: dict, out_dir: str, source_prompt: str = "") -> dict:
    """Draw one image per row and write the dataset directory.

    Deterministic: the RNG is seeded from the proposal content, so the same
    proposal renders byte-identical documents on every machine.
    """
    out = Path(out_dir)
    images = out / "images"
    images.mkdir(parents=True, exist_ok=True)
    fields, rows = proposal["fields"], proposal["rows"]

    seed = int(hashlib.sha256(json.dumps(proposal, sort_keys=True).encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)

    doc_ids = []
    with (out / "ground_truth.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["doc_id", *(f["name"] for f in fields)])
        writer.writeheader()
        for index, row in enumerate(rows, start=1):
            doc_id = f"doc_{index:03d}"
            _render_document(images / f"{doc_id}.png", proposal["document_kind"],
                             row, fields, index % 3, rng)
            writer.writerow({"doc_id": doc_id, **row})
            doc_ids.append(doc_id)

    (out / "schema.json").write_text(json.dumps(fields, indent=2), encoding="utf-8")
    manifest = {
        "title": proposal["title"],
        "description": proposal["description"],
        "document_kind": proposal["document_kind"],
        "generated_from_prompt": _clean_text(source_prompt, 500),
        "doc_count": len(doc_ids),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {"doc_ids": doc_ids, "manifest": manifest}
