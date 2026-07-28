"""Tests for the deterministic ProofBench evaluator."""

from __future__ import annotations

import csv
import json
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from engine.evaluate import (
    cer,
    evaluate_results,
    normalize_amount,
    normalize_date,
    normalize_text,
    token_f1,
)


@pytest.fixture
def tmp_path() -> Iterator[Path]:
    """Provide an isolated path without relying on the locked global temp root."""
    with tempfile.TemporaryDirectory(prefix="proofbench-test-", dir=Path(__file__).parent) as path:
        yield Path(path)


def test_normalize_text_case_punctuation_and_spacing() -> None:
    assert normalize_text("  ACME,   Pte. Ltd  ") == "acme pte ltd"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1 Jun 2026", "2026-06-01"),
        ("06/01/2026", "2026-06-01"),
        ("2026-06-01", "2026-06-01"),
        ("June 1, 2026", "2026-06-01"),
    ],
)
def test_normalize_date_formats(raw: str, expected: str) -> None:
    assert normalize_date(raw) == expected


def test_normalize_date_unparseable() -> None:
    assert normalize_date("not a date") == ""


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("$1,234.50", 123_450),
        ("SGD 128.5", 12_850),
        ("1.234,50", 123_450),
        ("(12.34)", -1_234),
    ],
)
def test_normalize_amount_currency(raw: str, expected: int) -> None:
    assert normalize_amount(raw) == expected


def test_normalize_amount_unparseable() -> None:
    assert normalize_amount("SGD unavailable") is None


def test_token_f1_exact() -> None:
    assert token_f1("Acme Pte Ltd", "acme pte ltd") == 1.0


def test_token_f1_partial_vendor_match() -> None:
    score = token_f1("Acme Ltd", "Acme Pte Ltd")
    assert 0.0 < score < 1.0


def test_cer_equal_strings() -> None:
    assert cer("invoice", "invoice") == 0.0


def test_cer_known_edit_distance() -> None:
    assert cer("kitten", "sitting") == pytest.approx(3 / 7)


def test_cer_empty_ground_truth_is_zero() -> None:
    assert cer("unexpected", "") == 0.0


def test_cer_empty_prediction() -> None:
    assert cer("", "abc") == 1.0


def test_evaluate_results_two_document_fixture(tmp_path) -> None:
    ground_truth_path = tmp_path / "ground_truth.csv"
    with ground_truth_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("doc_id", "invoice_number", "date", "vendor", "total"),
        )
        writer.writeheader()
        writer.writerows(
            [
                {
                    "doc_id": "inv_001",
                    "invoice_number": "INV-1001",
                    "date": "2026-06-01",
                    "vendor": "Acme Pte Ltd",
                    "total": "128.50",
                },
                {
                    "doc_id": "inv_002",
                    "invoice_number": "INV-1002",
                    "date": "2026-06-04",
                    "vendor": "Northstar Logistics LLP",
                    "total": "240.00",
                },
            ]
        )

    results_path = tmp_path / "results.jsonl"
    records = [
        {
            "candidate": "tesseract",
            "doc_id": "inv_001",
            "ok": True,
            "prediction": {
                "invoice_number": "INV-1001",
                "date": "06/01/2026",
                "vendor": "ACME, Pte Ltd",
                "total": "$128.50",
            },
            "latency_s": 1.0,
            "error": None,
        },
        {
            "candidate": "tesseract",
            "doc_id": "inv_002",
            "ok": True,
            "prediction": {
                "invoice_number": "INV-1002",
                "date": "2026-06-05",
                "vendor": "Northstar Logistics",
                "total": "240.00",
            },
            "latency_s": 3.0,
            "error": None,
        },
        {
            "candidate": "flaky_api",
            "doc_id": "inv_001",
            "ok": False,
            "prediction": None,
            "latency_s": 0.5,
            "error": "TimeoutError: timed out",
        },
        {
            "candidate": "flaky_api",
            "doc_id": "inv_002",
            "ok": True,
            "prediction": {
                "invoice_number": "INV-1002",
                "date": "4 Jun 2026",
                "vendor": "Northstar Logistics LLP",
                "total": "SGD 240",
            },
            "latency_s": 1.5,
            "error": None,
        },
    ]
    results_path.write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )

    metrics = evaluate_results(
        str(results_path), str(ground_truth_path), pricing={"flaky_api": 0.002}
    )

    assert metrics["tesseract"]["exact_accuracy"] == 0.75
    assert metrics["tesseract"]["field_f1"] > 0.75
    assert metrics["tesseract"]["mean_latency_s"] == 2.0
    assert metrics["tesseract"]["failure_rate"] == 0.0
    assert metrics["tesseract"]["setup_complexity"] == 2
    assert metrics["flaky_api"]["exact_accuracy"] == 0.5
    assert metrics["flaky_api"]["failure_rate"] == 0.5
    assert metrics["flaky_api"]["cost_per_1k_docs"] == 2.0
    assert metrics["flaky_api"]["n_docs"] == 2


def _fixture_ground_truth(tmp_path: Path) -> Path:
    path = tmp_path / "ground_truth.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["doc_id", "invoice_number", "date", "vendor", "total"]
        )
        writer.writeheader()
        writer.writerow(
            {
                "doc_id": "inv_001",
                "invoice_number": "INV-1001",
                "date": "2026-06-01",
                "vendor": "Acme Pte Ltd",
                "total": "128.50",
            }
        )
    return path


def test_candidate_that_never_ran_is_not_scored_zero(tmp_path) -> None:
    """A candidate with no successful document was not measured, so no score."""
    ground_truth_path = _fixture_ground_truth(tmp_path)
    results_path = tmp_path / "results.jsonl"
    results_path.write_text(
        json.dumps(
            {
                "candidate": "openai_vision",
                "doc_id": "inv_001",
                "ok": False,
                "prediction": None,
                "latency_s": 0.0,
                "error": "RateLimitError: Error code: 429 - quota exceeded",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    metrics = evaluate_results(str(results_path), str(ground_truth_path))["openai_vision"]

    assert metrics["status"] == "no_result"
    assert metrics["documents_scored"] == 0
    # Withheld, not zero: 0.0 would claim a measurement the run never made.
    assert metrics["exact_accuracy"] is None
    assert metrics["field_f1"] is None
    assert metrics["cer"] is None
    assert metrics["mean_latency_s"] is None
    assert metrics["failure_rate"] == 1.0
    assert "429" in metrics["error_summary"]


def test_unknown_cost_is_withheld_rather_than_reported_as_free(tmp_path) -> None:
    ground_truth_path = _fixture_ground_truth(tmp_path)
    results_path = tmp_path / "results.jsonl"
    results_path.write_text(
        json.dumps(
            {
                "candidate": "tesseract",
                "doc_id": "inv_001",
                "ok": True,
                "prediction": {
                    "invoice_number": "INV-1001",
                    "date": "2026-06-01",
                    "vendor": "Acme Pte Ltd",
                    "total": "128.50",
                },
                "latency_s": 1.0,
                "error": None,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    metrics = evaluate_results(str(results_path), str(ground_truth_path))["tesseract"]

    assert metrics["status"] == "ok"
    assert metrics["exact_accuracy"] == 1.0
    assert metrics["cost_per_1k_docs"] is None
