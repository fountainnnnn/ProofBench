"""The dataset designer validates model output and renders deterministically."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.dataset_gen import MIN_DOCS, _validated_proposal, render_dataset


def _proposal(**overrides):
    value = {
        "title": "Shipping labels",
        "description": "Parcel labels with carrier references",
        "document_kind": "shipping label",
        "fields": [
            {"name": "tracking_number", "type": "text"},
            {"name": "ship_date", "type": "date"},
            {"name": "weight_kg", "type": "number"},
        ],
        "rows": [
            {"tracking_number": f"TRK-90{i}", "ship_date": f"2026-07-0{i}",
             "weight_kg": str(1.5 + i)}
            for i in range(1, 7)
        ],
    }
    value.update(overrides)
    return value


def test_a_valid_proposal_passes_through_with_types():
    validated = _validated_proposal(_proposal(), 6)
    assert [f["type"] for f in validated["fields"]] == ["text", "date", "number"]
    assert len(validated["rows"]) == 6


def test_field_names_are_strictly_validated():
    for bad in ("Not Snake", "1leading", "x" * 65, ""):
        with pytest.raises(ValueError):
            _validated_proposal(_proposal(fields=[
                {"name": bad, "type": "text"}, {"name": "ok", "type": "text"}]), 6)


def test_unknown_types_fall_back_to_inference_not_errors():
    validated = _validated_proposal(_proposal(
        fields=[{"name": "date", "type": "geojson"}, {"name": "note", "type": "geojson"}],
        rows=[{"date": f"2026-07-0{i}", "note": f"note {i}"} for i in range(1, 7)]), 6)
    # 'date' carries its legacy typing; an unknown type on any other name is text.
    assert validated["fields"] == [
        {"name": "date", "type": "date"}, {"name": "note", "type": "text"}]


def test_incomplete_rows_are_dropped_and_too_few_rows_reject():
    rows = _proposal()["rows"]
    rows[0]["tracking_number"] = ""  # incomplete → dropped
    validated = _validated_proposal(_proposal(rows=rows), 6)
    assert len(validated["rows"]) == 5
    with pytest.raises(ValueError):
        _validated_proposal(_proposal(rows=rows[: MIN_DOCS - 1]), 6)


def test_rendering_is_deterministic_and_complete(tmp_path):
    proposal = _validated_proposal(_proposal(), 6)
    first, second = tmp_path / "a", tmp_path / "b"
    render_dataset(proposal, str(first), "compare label OCR")
    render_dataset(proposal, str(second), "compare label OCR")

    names = sorted(p.name for p in (first / "images").iterdir())
    assert names == [f"doc_{i:03d}.png" for i in range(1, 7)]
    for name in names:
        assert (first / "images" / name).read_bytes() == (second / "images" / name).read_bytes()

    header = (first / "ground_truth.csv").read_text(encoding="utf-8").splitlines()[0]
    assert header == "doc_id,tracking_number,ship_date,weight_kg"
    schema = json.loads((first / "schema.json").read_text(encoding="utf-8"))
    assert schema == proposal["fields"]
    manifest = json.loads((first / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["doc_count"] == 6
    assert manifest["generated_from_prompt"] == "compare label OCR"


class _Response:
    def __init__(self, content):
        self.choices = [type("C", (), {"message": type("M", (), {"content": content})()})()]


def test_a_rejected_proposal_is_asked_for_again_instead_of_failing_the_run(monkeypatch):
    """One malformed draw must not destroy a benchmark before it starts.

    The designer's output is validated strictly, and a single sample that
    renamed a column failed the whole measured run at start — before any
    candidate had been built or executed. Sampling accidents are retried.
    """
    from engine import dataset_gen

    good = json.dumps(_proposal())
    calls = []

    def fake_complete(env, messages, temperature):
        calls.append(messages)
        # First draw invents a column the pinned schema does not declare.
        if len(calls) == 1:
            return _Response(json.dumps(_proposal(fields=[
                {"name": "renamed_column", "type": "text"},
                {"name": "ship_date", "type": "date"},
            ])))
        return _Response(good)

    monkeypatch.setattr("engine.agent._orchestrator_complete", fake_complete)
    proposal = dataset_gen.propose_dataset("parcels", 6, env={})

    assert len(calls) == 2, "a rejected proposal should be requested again"
    assert len(proposal["rows"]) == 6
    # The retry is told what was wrong rather than repeating the same ask.
    assert any("rejected" in m["content"] for m in calls[1])


def test_the_designer_gives_up_with_the_real_reason_after_repeated_failures(monkeypatch):
    from engine import dataset_gen

    def always_bad(env, messages, temperature):
        return _Response("no json here")

    monkeypatch.setattr("engine.agent._orchestrator_complete", always_bad)
    with pytest.raises(ValueError, match="no JSON object"):
        dataset_gen.propose_dataset("parcels", 6, env={})
