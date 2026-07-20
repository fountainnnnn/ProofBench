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


FIELDS = ("invoice_number", "date", "vendor", "total")
SETUP_COMPLEXITY = {
    "tesseract": 2,
    "easyocr": 3,
    "nosana_vlm": 2,
    "doubleword": 2,
}


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


def _normalized_field(field: str, value: Any) -> str:
    if field == "date":
        return normalize_date(value)
    if field == "total":
        amount = normalize_amount(value)
        return "" if amount is None else str(amount)
    return normalize_text(value)


def _read_ground_truth(path: str) -> dict[str, dict[str, str]]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"doc_id", *FIELDS}
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise ValueError("ground truth CSV is missing required columns")
        return {str(row["doc_id"]): row for row in reader}


def _read_results(path: str) -> dict[str, dict[str, dict[str, Any]]]:
    grouped: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at line {line_number}: {exc.msg}") from exc
            if not isinstance(row, dict) or "candidate" not in row or "doc_id" not in row:
                raise ValueError(f"invalid result record at line {line_number}")
            grouped[str(row["candidate"])][str(row["doc_id"])] = row
    return grouped


def _rounded(value: float) -> float:
    return round(value, 6)


def evaluate_results(
    results_path: str,
    ground_truth_path: str,
    pricing: dict | None = None,
) -> dict:
    """Evaluate candidate JSONL results against the ground-truth CSV."""
    ground_truth = _read_ground_truth(ground_truth_path)
    results = _read_results(results_path)
    prices = pricing or {}
    n_docs = len(ground_truth)
    field_slots = n_docs * len(FIELDS)
    metrics: dict[str, dict[str, int | float]] = {}

    for candidate_name in sorted(results):
        candidate_rows = results[candidate_name]
        exact_matches = 0
        f1_total = 0.0
        cer_total = 0.0
        failures = 0
        latencies: list[float] = []

        for doc_id, gt_row in ground_truth.items():
            result = candidate_rows.get(doc_id)
            ok = bool(result and result.get("ok") is True and isinstance(result.get("prediction"), dict))
            if not ok:
                failures += 1
            prediction = result["prediction"] if ok else {}
            latency = result.get("latency_s", 0.0) if result else 0.0
            try:
                latencies.append(float(latency or 0.0))
            except (TypeError, ValueError):
                latencies.append(0.0)

            for field in FIELDS:
                pred_value = _normalized_field(field, prediction.get(field, ""))
                gt_value = _normalized_field(field, gt_row.get(field, ""))
                exact_matches += pred_value == gt_value
                f1_total += token_f1(pred_value, gt_value)
                cer_total += cer(pred_value, gt_value)

        denominator = field_slots or 1
        metrics[candidate_name] = {
            "exact_accuracy": _rounded(exact_matches / denominator) if field_slots else 0.0,
            "field_f1": _rounded(f1_total / denominator) if field_slots else 0.0,
            "cer": _rounded(cer_total / denominator) if field_slots else 0.0,
            "mean_latency_s": _rounded(sum(latencies) / n_docs) if n_docs else 0.0,
            "failure_rate": _rounded(failures / n_docs) if n_docs else 0.0,
            "cost_per_1k_docs": _rounded(float(prices.get(candidate_name, 0.0) or 0.0) * 1000),
            "setup_complexity": SETUP_COMPLEXITY.get(candidate_name, 1),
            "n_docs": n_docs,
        }
    return metrics
