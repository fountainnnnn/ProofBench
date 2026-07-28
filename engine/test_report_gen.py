"""The report must never present a candidate that did not run as a scored result."""

from __future__ import annotations

from engine.report_gen import _build_prompt, _fallback_report

MEASURED = {
    "exact_accuracy": 0.666667,
    "field_f1": 0.791534,
    "cer": 0.25465,
    "mean_latency_s": 0.665,
    "failure_rate": 0.0,
    "cost_per_1k_docs": None,
    "setup_complexity": 2,
    "n_docs": 15,
    "documents_scored": 15,
    "status": "ok",
}
NEVER_RAN = {
    "exact_accuracy": None,
    "field_f1": None,
    "cer": None,
    "mean_latency_s": None,
    "failure_rate": 1.0,
    "cost_per_1k_docs": None,
    "setup_complexity": 1,
    "n_docs": 15,
    "documents_scored": 0,
    "status": "no_result",
    "error_summary": "RateLimitError: Error code: 429 - quota exceeded",
}


def test_unmeasured_candidate_is_excluded_from_the_ranking() -> None:
    report = _fallback_report({"tesseract": MEASURED, "openai_vision": NEVER_RAN}, [])

    summary = report.split("## Did not run")[0]
    assert "| 1 | tesseract |" in summary
    assert "openai_vision" not in summary
    assert "## Did not run" in report
    assert "429" in report
    assert "**tesseract** ranks first" in report
    assert "1 candidate(s) could not be evaluated" in report


def test_a_run_where_nothing_ran_has_no_winner() -> None:
    report = _fallback_report({"openai_vision": NEVER_RAN, "mindee": dict(NEVER_RAN)}, [])

    assert "ranks first" not in report
    assert "no winner" in report


def test_prompt_forbids_describing_an_unmeasured_candidate_as_zero() -> None:
    prompt = _build_prompt({"tesseract": MEASURED, "openai_vision": NEVER_RAN}, [])

    assert "openai_vision" in prompt
    assert "do not describe them as scoring zero" in prompt
    # The ranked payload the model sees must not contain the candidate at all.
    ranked_block = prompt.split("RANKED METRICS JSON:")[1].split("UNMEASURED")[0]
    assert "openai_vision" not in ranked_block


def test_prompt_asks_for_a_no_winner_verdict_when_nothing_ran() -> None:
    prompt = _build_prompt({"openai_vision": NEVER_RAN}, [])

    assert "no winner" in prompt


def test_a_failing_provider_falls_through_to_the_next_one(monkeypatch, tmp_path) -> None:
    """One rate-limited provider must not downgrade every report to the bare table."""
    import engine.llm_clients as llm_clients
    import engine.report_gen as report_gen

    monkeypatch.setattr(llm_clients, "capability_providers",
                        lambda capability, env=None: ("openai", "deepseek"))
    attempted: list[str] = []

    def compose(provider, metrics, citations, runtime_env):
        attempted.append(provider)
        if provider == "openai":
            raise RuntimeError("429 quota exceeded")
        return "# Written by the healthy provider\n"

    monkeypatch.setattr(report_gen, "_compose", compose)
    out = tmp_path / "report.md"

    markdown = report_gen.write_report({"tesseract": MEASURED}, [], str(out))

    assert attempted == ["openai", "deepseek"]
    assert "healthy provider" in markdown
    assert out.read_text(encoding="utf-8") == markdown


def test_the_deterministic_table_is_used_only_when_every_provider_fails(monkeypatch, tmp_path) -> None:
    import engine.llm_clients as llm_clients
    import engine.report_gen as report_gen

    monkeypatch.setattr(llm_clients, "capability_providers",
                        lambda capability, env=None: ("openai", "deepseek"))
    monkeypatch.setattr(report_gen, "_compose",
                        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("down")))

    markdown = report_gen.write_report({"tesseract": MEASURED}, [], str(tmp_path / "r.md"))

    assert "# ProofBench Report" in markdown
    assert "**tesseract** ranks first" in markdown
