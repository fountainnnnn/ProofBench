from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from engine.demo_fallback import demo_metrics, demo_report, demo_spec, emit_demo_run


def test_demo_metrics_are_deterministic_and_labelled():
    spec = demo_spec("data/demo", "benchmark invoices")
    first = demo_metrics(spec, n_docs=15)
    second = demo_metrics(spec, n_docs=15)
    assert first == second
    assert first
    assert all(values["is_demo"] is True for values in first.values())
    assert all(values["n_docs"] == 15 for values in first.values())


def test_demo_report_identifies_deterministic_results():
    report = demo_report(demo_metrics(demo_spec("data/demo")))
    assert "Deterministic run" in report
    assert "Real mode" in report
    assert "Ranked results" in report


def test_emit_demo_run_reaches_done_and_persists():
    run_dir = Path("runs") / f"test_demo_fallback_{uuid.uuid4().hex[:8]}"
    events = []
    try:
        metrics, report = emit_demo_run(
            demo_spec("data/demo"),
            lambda event, data: events.append((event, data)),
            str(run_dir),
            n_docs=8,
            step_delay_s=0,
        )
        assert metrics
        assert report
        assert (run_dir / "metrics.json").exists()
        assert (run_dir / "report.md").exists()
        assert any(event == "artifact" and data.get("kind") == "results" for event, data in events)
        assert any(event == "state" and data.get("phase") == "DONE" for event, data in events)
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)
