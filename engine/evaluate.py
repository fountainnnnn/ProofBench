"""Deterministic extraction metrics for ProofBench."""

from __future__ import annotations

import csv
import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any


# The default schema. A run scores against the fields its spec declares; this
# stays the fallback so existing callers and stored runs keep their meaning.
FIELDS = ("invoice_number", "date", "vendor", "total")
SETUP_COMPLEXITY = {
    "tesseract": 2,
    "easyocr": 3,
    "nosana_vlm": 2,
    "doubleword": 2,
}
MAX_CANDIDATES = 100
MAX_DOCUMENTS = 10_000
MAX_FIELD_CHARS = 2048
MAX_IDENTIFIER_CHARS = 128
MAX_RESULTS_BYTES = 16 * 1024 * 1024
MAX_GROUND_TRUTH_BYTES = 8 * 1024 * 1024
MAX_RESULT_RECORDS = 200_000
MAX_LEVENSHTEIN_CELLS = 4_194_304
MAX_EVALUATION_CELLS = 50_000_000


def _checked_file_size(path: Path, limit: int, label: str) -> None:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ValueError(f"{label} is unavailable") from exc
    if size > limit:
        raise ValueError(f"{label} exceeds the allowed size")


def normalize_text(s: str) -> str:
    """Case-fold text, remove punctuation, and collapse whitespace."""
    if s is None:
        return ""
    value = unicodedata.normalize("NFKC", str(s)).casefold()
    value = "".join(char if char.isalnum() else " " for char in value)
    return " ".join(value.split())


def normalize_date(s: str) -> str:
    """Convert common invoice date formats to ISO YYYY-MM-DD."""
    if s is None:
        return ""
    value = " ".join(str(s).strip().replace(",", " ").split())
    if not value:
        return ""

    formats = (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y.%m.%d",
        "%d %b %Y",
        "%d %B %Y",
        "%d-%b-%Y",
        "%d-%B-%Y",
        "%b %d %Y",
        "%B %d %Y",
        "%m/%d/%Y",
        "%m-%d-%Y",
        "%m/%d/%y",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%d/%m/%y",
    )
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def normalize_amount(s: str) -> int | None:
    """Parse a currency-like value and return integer cents."""
    if s is None:
        return None
    raw = unicodedata.normalize("NFKC", str(s)).strip()
    if not raw or not re.search(r"\d", raw):
        return None

    negative = bool(re.search(r"^\s*-", raw)) or (
        raw.lstrip().startswith("(") and raw.rstrip().endswith(")")
    )
    value = re.sub(r"[^\d.,]", "", raw)
    if not value:
        return None

    if "." in value and "," in value:
        decimal_mark = "." if value.rfind(".") > value.rfind(",") else ","
        thousands_mark = "," if decimal_mark == "." else "."
        value = value.replace(thousands_mark, "").replace(decimal_mark, ".")
    elif "." in value or "," in value:
        mark = "." if "." in value else ","
        parts = value.split(mark)
        if len(parts) == 2 and len(parts[1]) in (1, 2):
            value = f"{parts[0]}.{parts[1]}"
        elif len(parts) > 2 and len(parts[-1]) in (1, 2):
            value = f"{''.join(parts[:-1])}.{parts[-1]}"
        else:
            value = "".join(parts)

    try:
        cents = int((Decimal(value) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError):
        return None
    return -cents if negative else cents


def token_f1(pred: str, gt: str) -> float:
    """Return multiset token F1 after text normalization."""
    pred_tokens = normalize_text(pred).split()
    gt_tokens = normalize_text(gt).split()
    if not pred_tokens and not gt_tokens:
        return 1.0
    if not pred_tokens or not gt_tokens:
        return 0.0
    overlap = sum((Counter(pred_tokens) & Counter(gt_tokens)).values())
    precision = overlap / len(pred_tokens)
    recall = overlap / len(gt_tokens)
    return 2 * precision * recall / (precision + recall) if overlap else 0.0


def cer(pred: str, gt: str) -> float:
    """Return Levenshtein distance divided by ground-truth length."""
    pred_value = "" if pred is None else str(pred)
    gt_value = "" if gt is None else str(gt)
    if not gt_value:
        return 0.0
    if len(pred_value) * len(gt_value) > MAX_LEVENSHTEIN_CELLS:
        raise ValueError("CER input exceeds the evaluation work limit")

    previous = list(range(len(gt_value) + 1))
    for pred_index, pred_char in enumerate(pred_value, start=1):
        current = [pred_index]
        for gt_index, gt_char in enumerate(gt_value, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[gt_index] + 1,
                    previous[gt_index - 1] + (pred_char != gt_char),
                )
            )
        previous = current
    return previous[-1] / max(len(gt_value), 1)


def _normalized_field(field: Any, value: Any) -> str:
    """Canonicalize one value for comparison.

    Accepts a typed Field or a bare name; a bare name is typed the way it always
    was, so legacy callers normalize identically.
    """
    from engine.fields import Field, infer_type, normalize_value

    if not isinstance(field, Field):
        field = Field(str(field), infer_type(str(field)))
    return normalize_value(field, value)


def _read_ground_truth(path: str, fields: tuple = FIELDS) -> dict[str, dict[str, str]]:
    source = Path(path)
    _checked_file_size(source, MAX_GROUND_TRUTH_BYTES, "ground truth")
    try:
        handle = source.open("r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise ValueError("ground truth is unavailable") from exc
    with handle:
        reader = csv.DictReader(handle)
        required = {"doc_id", *(getattr(f, "name", f) for f in fields)}
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise ValueError("ground truth CSV is missing required columns")
        rows: dict[str, dict[str, str]] = {}
        for row in reader:
            doc_id = str(row.get("doc_id") or "")
            if not doc_id or len(doc_id) > MAX_IDENTIFIER_CHARS:
                raise ValueError("invalid ground-truth document identifier")
            if doc_id in rows:
                raise ValueError("duplicate ground-truth document")
            if any(len(str(row.get(getattr(f, "name", f)) or "")) > MAX_FIELD_CHARS
                   for f in fields):
                raise ValueError("ground-truth field exceeds the allowed size")
            rows[doc_id] = row
            if len(rows) > MAX_DOCUMENTS:
                raise ValueError("ground truth exceeds the document limit")
        return rows


def _read_results(path: str) -> dict[str, dict[str, dict[str, Any]]]:
    grouped: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    source = Path(path)
    _checked_file_size(source, MAX_RESULTS_BYTES, "results")
    record_count = 0
    try:
        handle = source.open("r", encoding="utf-8")
    except OSError as exc:
        raise ValueError("results are unavailable") from exc
    with handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at line {line_number}: {exc.msg}") from exc
            if not isinstance(row, dict) or "candidate" not in row or "doc_id" not in row:
                raise ValueError(f"invalid result record at line {line_number}")
            candidate = str(row["candidate"])
            doc_id = str(row["doc_id"])
            if (
                not candidate
                or not doc_id
                or len(candidate) > MAX_IDENTIFIER_CHARS
                or len(doc_id) > MAX_IDENTIFIER_CHARS
            ):
                raise ValueError("invalid result identifier")
            prediction = row.get("prediction")
            if prediction is not None:
                if not isinstance(prediction, dict):
                    raise ValueError("invalid result prediction")
                if any(
                    len(str(value or "")) > MAX_FIELD_CHARS
                    for value in prediction.values()
                ):
                    raise ValueError("result field exceeds the allowed size")
            if len(str(row.get("error") or "")) > MAX_FIELD_CHARS:
                raise ValueError("result error exceeds the allowed size")
            if doc_id in grouped[candidate]:
                raise ValueError("duplicate result record")
            grouped[candidate][doc_id] = row
            record_count += 1
            if record_count > MAX_RESULT_RECORDS:
                raise ValueError("results exceed the record limit")
            if len(grouped) > MAX_CANDIDATES:
                raise ValueError("results exceed the candidate limit")
    return grouped


def _rounded(value: float) -> float:
    return round(value, 6)


def evaluate_results(
    results_path: str,
    ground_truth_path: str,
    pricing: dict | None = None,
    fields: Any = None,
) -> dict:
    """Evaluate candidate JSONL results against the ground-truth CSV.

    ``fields`` is the benchmark's extraction schema. Omitted, it is the invoice
    schema this product started with, so existing callers are unaffected.
    """
    from engine.fields import parse_fields

    schema = parse_fields(fields)
    ground_truth = _read_ground_truth(ground_truth_path, schema)
    results = _read_results(results_path)
    prices = pricing or {}
    n_docs = len(ground_truth)
    field_slots = n_docs * len(schema)
    metrics: dict[str, dict[str, int | float]] = {}
    evaluation_cells = 0

    for candidate_name in sorted(results):
        candidate_rows = results[candidate_name]
        exact_matches = 0
        f1_total = 0.0
        cer_total = 0.0
        failures = 0
        latencies: list[float] = []
        errors: Counter[str] = Counter()

        for doc_id, gt_row in ground_truth.items():
            result = candidate_rows.get(doc_id)
            ok = bool(result and result.get("ok") is True and isinstance(result.get("prediction"), dict))
            if not ok:
                failures += 1
                errors[str((result or {}).get("error") or "no result was produced")] += 1
            prediction = result["prediction"] if ok else {}
            latency = result.get("latency_s", 0.0) if result else 0.0
            try:
                latencies.append(float(latency or 0.0))
            except (TypeError, ValueError):
                latencies.append(0.0)

            for field in schema:
                pred_value = _normalized_field(field, prediction.get(field.name, ""))
                gt_value = _normalized_field(field, gt_row.get(field.name, ""))
                evaluation_cells += max(len(pred_value), 1) * max(len(gt_value), 1)
                if evaluation_cells > MAX_EVALUATION_CELLS:
                    raise ValueError("evaluation exceeds the total work limit")
                exact_matches += pred_value == gt_value
                f1_total += token_f1(pred_value, gt_value)
                cer_total += cer(pred_value, gt_value)

        denominator = field_slots or 1
        scored = n_docs - failures
        # A candidate that never produced a single result was not measured.
        # Reporting 0.0 accuracy there states a benchmark outcome the run never
        # observed, so the quality metrics are withheld and the reason is named.
        ran = scored > 0
        price = prices.get(candidate_name)
        metrics[candidate_name] = {
            "exact_accuracy": _rounded(exact_matches / denominator) if ran and field_slots else None,
            "field_f1": _rounded(f1_total / denominator) if ran and field_slots else None,
            "cer": _rounded(cer_total / denominator) if ran and field_slots else None,
            "mean_latency_s": _rounded(sum(latencies) / n_docs) if ran and n_docs else None,
            "failure_rate": _rounded(failures / n_docs) if n_docs else 0.0,
            "cost_per_1k_docs": _rounded(float(price) * 1000) if price is not None else None,
            "setup_complexity": SETUP_COMPLEXITY.get(candidate_name, 1),
            "n_docs": n_docs,
            "documents_scored": scored,
            "status": "ok" if ran else "no_result",
        }
        if errors:
            metrics[candidate_name]["error_summary"] = errors.most_common(1)[0][0][:MAX_FIELD_CHARS]
    return metrics
