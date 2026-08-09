import asyncio
import json
import shutil
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from engine.agent import Orchestrator
from engine import agent, llm_clients, tool_assessment
from engine.tool_assessment import (
    assess_documentation_batch,
    format_constraints,
    result_from_plan,
    unavailable_result,
    validate_plan,
    write_assessment_report,
)


def plan(**overrides):
    value = {
        "implementable": True,
        "reason": "The official SDK documents installation and client construction.",
        "documentation_quality": 80,
        "integration_feasibility": 84,
        "auth_clarity": 70,
        "setup_complexity": 2,
        "build_commands": ["pip install example-sdk"],
        "verification_code": "import example\nprint('PROOFBENCH_OK')",
        "evidence": ["The SDK has a documented Python package."],
    }
    value.update(overrides)
    return value


def test_verified_implementation_uses_daytona_and_gets_bonus():
    result = result_from_plan(validate_plan(plan()), "passed", True)
    assert result["rating"] == 90
    assert result["implementable"] is True
    assert result["daytona_triggered"] is True


def test_non_implementable_plan_cannot_trigger_daytona():
    value = validate_plan(plan(
        implementable=False,
        build_commands=["pip install should-be-removed"],
        verification_code="print('PROOFBENCH_OK')",
        integration_feasibility=40,
    ))
    result = result_from_plan(value, "not_implementable", False)
    assert value["build_commands"] == []
    assert value["verification_code"] == ""
    assert result["rating"] <= 49
    assert result["daytona_triggered"] is False


def test_implementable_plan_requires_verification_code():
    with pytest.raises(ValueError, match="verification_code"):
        validate_plan(plan(verification_code=""))


def test_unavailable_docs_withhold_scores_instead_of_faking_zeros():
    """A failure is not evidence about the tool, so no number is invented."""
    result = unavailable_result("documentation could not be scraped")
    assert result["rating"] is None
    assert result["suitability"] is None
    assert result["documentation_quality"] is None
    assert result["integration_feasibility"] is None
    assert result["auth_clarity"] is None
    assert result["setup_complexity"] is None
    assert result["assessment_basis"] == "unavailable"
    assert result["verification_status"] == "unavailable"
    assert result["daytona_triggered"] is False


def test_scores_are_bounded():
    with pytest.raises(ValueError, match="documentation_quality"):
        validate_plan(plan(documentation_quality=101))


# --------------------------------------------------- execution mode semantics


def test_comparison_only_candidate_keeps_a_legitimate_score_and_is_never_executed():
    """A SaaS product is scored from docs and is not penalised for being unrunnable."""
    value = validate_plan(plan(
        execution_mode="comparison_only",
        build_commands=["pip install should-be-removed"],
        verification_code="print('PROOFBENCH_OK')",
    ))
    # Nothing runnable survives validation for a comparison-only candidate.
    assert value["build_commands"] == []
    assert value["verification_code"] == ""

    result = result_from_plan(value, "not_applicable", False)
    # 0.30*80 + 0.50*84 + 0.20*70 == 80, with no unrunnability penalty.
    assert result["rating"] == 80
    assert result["suitability"] == 80
    assert result["execution_mode"] == "comparison_only"
    assert result["assessment_basis"] == "documentation_evidence"
    assert result["daytona_triggered"] is False
    assert result["verification_status"] == "not_applicable"


def test_sandbox_verified_candidate_reports_execution_basis():
    result = result_from_plan(validate_plan(plan()), "passed", True)
    assert result["execution_mode"] == "sandbox_verifiable"
    assert result["assessment_basis"] == "sandbox_execution"


def test_execution_mode_is_inferred_when_the_model_omits_it():
    assert validate_plan(plan())["execution_mode"] == "sandbox_verifiable"
    assert validate_plan(plan(
        implementable=False, build_commands=[], verification_code="",
    ))["execution_mode"] == "comparison_only"


def test_unknown_execution_mode_is_rejected():
    with pytest.raises(ValueError, match="execution_mode"):
        validate_plan(plan(execution_mode="pretend_we_ran_it"))


def test_comparison_only_candidate_never_provisions_a_sandbox(monkeypatch):
    """The runner must not acquire Daytona for a documentation-only comparison."""
    events = []
    run_dir = Path("runs") / f"test_comparison_only_{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(agent, "dispatch_tool", lambda *_a, **_k: json.dumps("official docs"))
    monkeypatch.setattr(
        tool_assessment,
        "assess_documentation_batch",
        lambda candidates, *_a, **_k: {
            candidate["name"]: {"plan": validate_plan(plan(execution_mode="comparison_only"))}
            for candidate in candidates
        },
    )
    orchestrator = Orchestrator(
        "test-comparison", str(run_dir), lambda event, data: events.append((event, data))
    )

    def fail_if_acquired(_name):
        raise AssertionError("comparison-only candidates must never reach Daytona")

    monkeypatch.setattr(orchestrator.pool, "acquire", fail_if_acquired)
    try:
        metrics = orchestrator.run_benchmark({
            "benchmark_type": "tool_assessment",
            "category": "CRM",
            "objective": "Compare hosted CRM integrations",
            "candidates": [{"name": "example", "display_name": "Example",
                            "docs_url": "https://example.com/docs", "kind": "saas"}],
        })
        row = metrics["example"]
        assert row["daytona_triggered"] is False
        assert row["assessment_basis"] == "documentation_evidence"
        assert isinstance(row["rating"], int) and 0 <= row["rating"] <= 100
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


def test_provider_failure_withholds_scores_instead_of_zeroing_every_candidate(monkeypatch):
    """An assessment outage must not read as 'every tool scored zero'."""
    events = []
    run_dir = Path("runs") / f"test_provider_failure_{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(agent, "dispatch_tool", lambda *_a, **_k: json.dumps("official docs"))

    def explode(*_args, **_kwargs):
        raise RuntimeError("provider unreachable")

    monkeypatch.setattr(tool_assessment, "assess_documentation_batch", explode)
    orchestrator = Orchestrator(
        "test-outage", str(run_dir), lambda event, data: events.append((event, data))
    )
    try:
        metrics = orchestrator.run_benchmark({
            "benchmark_type": "tool_assessment",
            "category": "CRM",
            "objective": "Compare hosted CRM integrations",
            "candidates": [
                {"name": "alpha", "docs_url": "https://example.com/a"},
                {"name": "beta", "docs_url": "https://example.com/b"},
            ],
        })
        for row in metrics.values():
            assert row["rating"] is None
            assert row["documentation_quality"] is None
            assert row["assessment_basis"] == "unavailable"
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


def test_assessment_falls_back_to_the_next_configured_provider(monkeypatch):
    """A dead primary provider hands off rather than collapsing the whole batch."""
    calls = []

    async def fake_provider_completions(provider, requests, model=None, env=None):
        calls.append(provider)
        if provider == "doubleword":
            raise RuntimeError("doubleword unreachable")
        content = json.dumps(plan(execution_mode="comparison_only"))
        return [
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
            for _ in requests
        ]

    monkeypatch.setattr(llm_clients, "provider_chat_completions", fake_provider_completions)
    results = assess_documentation_batch(
        [{"name": "alpha", "docs_text": "Alpha docs"}],
        "compare integrations",
        env={"DOUBLEWORD_API_KEY": "hidden", "OPENROUTER_API_KEY": "hidden"},
    )

    assert calls == ["doubleword", "openrouter"]
    assert results["alpha"]["plan"]["execution_mode"] == "comparison_only"


def test_assessment_timeout_falls_back_to_the_next_provider(monkeypatch):
    """A provider that never completes cannot hold the whole pipeline open."""
    calls = []

    async def fake_provider_completions(provider, requests, model=None, env=None):
        calls.append(provider)
        if provider == "doubleword":
            await asyncio.sleep(0.05)
        content = json.dumps(plan(execution_mode="comparison_only"))
        return [
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
            for _ in requests
        ]

    monkeypatch.setattr(llm_clients, "provider_chat_completions", fake_provider_completions)
    results = assess_documentation_batch(
        [{"name": "alpha", "docs_text": "Alpha docs"}],
        "compare integrations",
        env={
            "DOUBLEWORD_API_KEY": "hidden",
            "OPENROUTER_API_KEY": "hidden",
            "ASSESSMENT_PROVIDER_TIMEOUT_SECONDS": "0.01",
        },
    )

    assert calls == ["doubleword", "openrouter"]
    assert results["alpha"]["plan"]["execution_mode"] == "comparison_only"


def test_assessment_requires_a_configured_provider():
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        assess_documentation_batch(
            [{"name": "alpha", "docs_text": "docs"}], "objective", env={}
        )


def test_openrouter_alone_serves_every_llm_capability():
    from engine.llm_clients import capability_providers, resolve_provider

    env = {"OPENROUTER_API_KEY": "hidden-test-value"}
    for capability in ("orchestration", "assessment", "report", "codegen"):
        assert capability_providers(capability, env) == ("openrouter",)
        assert resolve_provider(capability, env) == "openrouter"


def _intake(dataset_available=False, run_dir="runs"):
    return Orchestrator(
        f"intake-{uuid.uuid4().hex[:8]}", run_dir, lambda _event, _data: None,
        dataset_available=dataset_available,
    )


def test_real_intake_extracts_nested_candidate_spec(tmp_path):
    spec = _intake(run_dir=str(tmp_path))._extract_spec(
        'Ready.\n```json\n{"category":"CRM","objective":"sync accounts",'
        '"candidates":[{"name":"alpha","docs_url":"https://example.com/docs"}]}\n```'
    )
    assert spec is not None
    assert spec["benchmark_type"] == "tool_assessment"
    assert spec["candidates"][0]["name"] == "alpha"


def test_intake_builds_a_measured_spec_when_labelled_data_is_bound(tmp_path):
    """A measurable objective plus ground truth is a scored run, not a docs rating."""
    text = ('```json\n{"category":"Invoice OCR",'
            '"fields":["invoice_number","date","vendor","total"],'
            '"candidates":[{"name":"tesseract","kind":"local_tool","use_fallback":true}]}\n```')

    spec = _intake(dataset_available=True, run_dir=str(tmp_path))._extract_spec(text)
    assert spec["benchmark_type"] == "extraction"
    # Bare names normalize to typed fields carrying their legacy typing, so the
    # evaluator compares dates as dates and totals as amounts, as it always did.
    assert spec["fields"] == [
        {"name": "invoice_number", "type": "text"},
        {"name": "date", "type": "date"},
        {"name": "vendor", "type": "text"},
        {"name": "total", "type": "currency"},
    ]
    # Bound data needs nothing built.
    assert "dataset" not in spec


def test_measured_spec_survives_without_bound_data_and_asks_for_examples(tmp_path):
    """No attached data is not a reason to answer a different question.

    The spec keeps its measured kind and its declared schema, and records that
    its labelled examples are still to be built. The old behaviour rewrote it to
    an assessment, which silently answered "which tools exist" when the user had
    asked "which one is more accurate".
    """
    text = ('```json\n{"benchmark_type":"extraction","category":"Parking ticket reading",'
            '"fields":[{"name":"plate_number","type":"text"},'
            '{"name":"fine_amount","type":"currency"}],'
            '"candidates":[{"name":"tesseract","kind":"local_tool","use_fallback":true}]}\n```')

    spec = _intake(dataset_available=False, run_dir=str(tmp_path))._extract_spec(text)
    assert spec["benchmark_type"] == "extraction"
    assert spec["fields"] == [
        {"name": "plate_number", "type": "text"},
        {"name": "fine_amount", "type": "currency"},
    ]
    assert spec["dataset"] == {"source": "generate"}


def test_assessment_intake_normalizes_pricing_placeholders_and_rejects_bad_docs(tmp_path):
    intake = _intake(run_dir=str(tmp_path))
    valid = intake._extract_spec(
        '```json\n{"benchmark_type":"tool_assessment","category":"HTTP clients",'
        '"objective":"compare integrations","candidates":[{"name":"Requests/HTTPX",'
        '"display_name":"Requests / HTTPX","docs_url":"https://www.python-httpx.org/",'
        '"pricing_url":"Open-source","kind":"saas"}]}\n```'
    )
    assert valid is not None
    assert valid["candidates"][0]["name"] == "Requests-HTTPX"
    assert valid["candidates"][0]["pricing_url"] == ""

    invalid = intake._extract_spec(
        '```json\n{"benchmark_type":"tool_assessment","category":"HTTP clients",'
        '"objective":"compare integrations","candidates":[{"name":"httpx",'
        '"display_name":"HTTPX","docs_url":"Open-source","kind":"saas"}]}\n```'
    )
    assert invalid is None


def test_both_benchmark_kinds_are_offered_whether_or_not_data_is_bound():
    """Which benchmark a question deserves is a property of the question.

    Bound data changes only where the labelled examples come from: it pins the
    spec to columns that already have ground truth, and its absence lets intake
    declare the schema so the run can build examples for it.
    """
    from engine.agent import intake_system

    for prompt in (intake_system(True), intake_system(False)):
        assert '"benchmark_type": "extraction"' in prompt
        assert '"benchmark_type": "tool_assessment"' in prompt

    bound = intake_system(True, [{"name": "plate_number", "type": "text"},
                                 {"name": "fine_amount", "type": "currency"}])
    assert "plate_number" in bound
    assert "MUST be exactly the labelled columns" in bound
    assert "ProofBench builds them" in intake_system(False)


def test_real_assessments_use_doubleword_autobatcher_and_configured_model(monkeypatch):
    captured = {}

    async def fake_batch(provider, requests, model=None, env=None):
        captured.update({"requests": requests, "model": model, "env": env})
        content = json.dumps(plan())
        return [
            SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
            )
            for _ in requests
        ]

    monkeypatch.setattr(llm_clients, "_concurrent_chat_completions", fake_batch)
    results = assess_documentation_batch(
        [
            {"name": "alpha", "docs_text": "Alpha SDK docs"},
            {"name": "beta", "docs_text": "Beta API docs"},
        ],
        "compare integrations",
        env={
            "DOUBLEWORD_API_KEY": "hidden-test-value",
            "DOUBLEWORD_MODEL": "deepseek-ai/DeepSeek-V4-Pro",
            "ACME_API_KEY": "another-hidden-value",
        },
        entitled_credentials=("ACME_API_KEY",),
    )

    assert captured["model"] == "deepseek-ai/DeepSeek-V4-Pro"
    assert len(captured["requests"]) == 2
    prompt = captured["requests"][0]["messages"][1]["content"]
    assert "ACME_API_KEY" in prompt
    assert "another-hidden-value" not in prompt
    assert results["alpha"]["plan"]["implementable"] is True


def test_advertised_credentials_are_exactly_the_verification_entitlements(monkeypatch):
    """A plan may only rely on variables the verification sandbox will supply."""
    captured = {}

    async def fake_batch(provider, requests, model=None, env=None):
        captured["requests"] = requests
        content = json.dumps(plan())
        return [
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
            for _ in requests
        ]

    monkeypatch.setattr(llm_clients, "_concurrent_chat_completions", fake_batch)
    env = {
        "DOUBLEWORD_API_KEY": "hidden-test-value",
        "OPENAI_API_KEY": "hidden-test-value",
        "OXYLABS_PASSWORD": "hidden-test-value",
    }
    assess_documentation_batch(
        [{"name": "alpha", "docs_text": "Alpha SDK docs"}],
        "compare integrations",
        env=env,
        entitled_credentials=tool_assessment.ASSESSMENT_VERIFICATION_ENTITLEMENTS,
    )

    prompt = captured["requests"][0]["messages"][1]["content"]
    # Generated verification code is entitled to nothing, so nothing is offered.
    assert tool_assessment.ASSESSMENT_VERIFICATION_ENTITLEMENTS == frozenset()
    assert "(none)" in prompt
    for name in env:
        assert name not in prompt


def test_assessment_verification_code_receives_no_credentials():
    """env_prelude injects nothing under the assessment entitlement set."""
    from engine.tools import env_prelude

    code = 'import os\nprint(os.environ["OPENAI_API_KEY"])'
    injected = env_prelude(
        code,
        {"OPENAI_API_KEY": "hidden-test-value"},
        tool_assessment.ASSESSMENT_VERIFICATION_ENTITLEMENTS,
    )
    assert injected == code
    assert "hidden-test-value" not in injected


def test_real_runner_skips_daytona_when_docs_are_not_implementable(monkeypatch):
    events = []
    run_dir = Path("runs") / f"test_tool_assessment_{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(agent, "dispatch_tool", lambda *_args, **_kwargs: json.dumps("official docs"))
    monkeypatch.setattr(
        tool_assessment,
        "assess_documentation_batch",
        lambda candidates, *_args, **_kwargs: {
            candidate["name"]: {"plan": validate_plan(plan(
                implementable=False,
                build_commands=[],
                verification_code="",
                integration_feasibility=30,
            ))}
            for candidate in candidates
        },
    )
    orchestrator = Orchestrator("test-real", str(run_dir), lambda event, data: events.append((event, data)))

    def fail_if_acquired(_name):
        raise AssertionError("Daytona must not be acquired for a non-implementable tool")

    monkeypatch.setattr(orchestrator.pool, "acquire", fail_if_acquired)
    try:
        metrics = orchestrator.run_benchmark({
            "benchmark_type": "tool_assessment",
            "category": "Collaboration",
            "objective": "Assess an internal messaging integration",
            "candidates": [{
                "name": "example",
                "display_name": "Example",
                "docs_url": "https://example.com/docs",
                "kind": "saas",
            }],
        })

        assert metrics["example"]["daytona_triggered"] is False
        assert metrics["example"]["verification_status"] == "not_implementable"
        assert any(event == "artifact" and data.get("kind") == "results" for event, data in events)
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


def test_unreadable_documentation_is_withheld_not_scored_zero():
    """A 404 docs URL says nothing about the vendor, so it earns no rating."""
    from engine.tool_assessment import result_from_plan

    plan = {
        "implementable": False,
        "execution_mode": "comparison_only",
        "documentation_quality": 0,
        "integration_feasibility": 0,
        "auth_clarity": 0,
        "setup_complexity": 5,
        "reason": "The documentation link leads to a 404 - Not Found page.",
        "evidence": ["The documentation page returned a 404 error."],
    }

    row = result_from_plan(plan, verification_status="not_implementable",
                           daytona_triggered=False)

    assert row["rating"] is None
    assert row["suitability"] is None
    assert row["verification_status"] == "unavailable"
    assert "404" in row["reason"]


def test_a_weak_but_readable_document_set_is_still_scored():
    """Withholding must apply only when nothing at all could be assessed."""
    from engine.tool_assessment import result_from_plan

    plan = {
        "implementable": False,
        "execution_mode": "comparison_only",
        "documentation_quality": 10,
        "integration_feasibility": 0,
        "auth_clarity": 0,
        "setup_complexity": 5,
        "reason": "Sparse documentation with no authentication guidance.",
        "evidence": ["Only a marketing overview is published."],
    }

    row = result_from_plan(plan, verification_status="not_implementable",
                           daytona_triggered=False)

    assert row["rating"] == 3
    assert row["implementable"] is False


# ------------------------------------------------------------ the pricing axis


def test_pricing_evidence_reweights_the_rating():
    """25/45/15/15 once pricing was actually measured."""
    value = validate_plan(plan(execution_mode="comparison_only",
                               pricing_transparency=60,
                               pricing_notes="Published per-seat tiers with a free plan."))
    row = result_from_plan(value, "not_applicable", False)

    # 0.25*80 + 0.45*84 + 0.15*70 + 0.15*60 == 77.3
    assert row["rating"] == 77
    assert row["pricing_transparency"] == 60
    assert row["pricing_notes"] == "Published per-seat tiers with a free plan."


def test_absent_pricing_evidence_is_never_a_penalty():
    """No pricing page means the legacy 30/50/20 weighting, not a zero axis."""
    without = result_from_plan(
        validate_plan(plan(execution_mode="comparison_only")), "not_applicable", False)
    explicit_null = result_from_plan(
        validate_plan(plan(execution_mode="comparison_only", pricing_transparency=None)),
        "not_applicable", False)

    assert without["rating"] == 80 == explicit_null["rating"]
    assert without["pricing_transparency"] is None
    # A zeroed pricing axis would score strictly lower, which is the claim the
    # withheld score exists to avoid making.
    zeroed = result_from_plan(
        validate_plan(plan(execution_mode="comparison_only", pricing_transparency=0)),
        "not_applicable", False)
    assert zeroed["rating"] < without["rating"]


def test_pricing_fields_are_optional_and_bounded():
    assert validate_plan(plan())["pricing_transparency"] is None
    assert validate_plan(plan())["pricing_notes"] == ""
    assert validate_plan(plan(pricing_transparency=None))["pricing_transparency"] is None
    assert validate_plan(plan(pricing_transparency=0))["pricing_transparency"] == 0
    with pytest.raises(ValueError, match="pricing_transparency"):
        validate_plan(plan(pricing_transparency=101))
    with pytest.raises(ValueError, match="pricing_transparency"):
        validate_plan(plan(pricing_transparency=-1))
    # Required keys must not grow: a provider that ignores pricing still validates.
    assert "pricing_transparency" not in tool_assessment.REQUIRED_PLAN_KEYS
    assert "pricing_notes" not in tool_assessment.REQUIRED_PLAN_KEYS


def test_pricing_does_not_rescue_a_row_with_no_readable_documentation():
    """The withhold check reads the three core axes only."""
    row = result_from_plan(
        {"implementable": False, "execution_mode": "comparison_only",
         "documentation_quality": 0, "integration_feasibility": 0, "auth_clarity": 0,
         "pricing_transparency": 90, "setup_complexity": 5,
         "reason": "The documentation link leads to a 404.", "evidence": []},
        "not_implementable", False)

    assert row["rating"] is None
    assert row["pricing_transparency"] is None


def test_unavailable_result_withholds_pricing_too():
    row = unavailable_result("documentation could not be scraped")
    assert row["pricing_transparency"] is None
    assert row["pricing_notes"] == ""


def test_pricing_page_reaches_the_prompt_only_when_one_was_scraped(monkeypatch):
    captured = {}

    async def fake_batch(provider, requests, model=None, env=None):
        captured["requests"] = requests
        content = json.dumps(plan())
        return [
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
            for _ in requests
        ]

    monkeypatch.setattr(llm_clients, "_concurrent_chat_completions", fake_batch)
    assess_documentation_batch(
        [
            {"name": "alpha", "docs_text": "Alpha docs", "pricing_text": "Free tier, $9/seat"},
            {"name": "beta", "docs_text": "Beta docs"},
        ],
        "compare integrations",
        env={"DOUBLEWORD_API_KEY": "hidden-test-value",
             },
        constraints={"stack": ["Python"], "budget": "under $500/month"},
    )

    with_pricing = captured["requests"][0]["messages"][1]["content"]
    without_pricing = captured["requests"][1]["messages"][1]["content"]
    assert "PRICING PAGE:" in with_pricing and "$9/seat" in with_pricing
    assert "PRICING PAGE:" not in without_pricing
    # The stated environment is threaded into every request, not just the first.
    for prompt in (with_pricing, without_pricing):
        assert "Existing stack: Python" in prompt
        assert "under $500/month" in prompt


def test_format_constraints_renders_only_what_was_stated():
    assert format_constraints({}) == ""
    assert format_constraints(None) == ""
    assert format_constraints("not a dict") == ""
    rendered = format_constraints({"stack": ["Python", "Postgres"], "deployment": "on-prem"})
    assert "Existing stack: Python, Postgres" in rendered
    assert "Deployment: on-prem" in rendered
    # Nothing is invented for the fields the user never answered.
    assert "Hard requirements" not in rendered
    assert "Budget" not in rendered


# ------------------------------------------------------------------- the report


def test_report_renders_pricing_as_a_column_and_withholds_it_honestly(tmp_path):
    metrics = {
        "alpha": {"rating": 77, "implementable": True, "display_name": "Alpha",
                  "assessment_basis": "documentation_evidence", "verification_status": "not_applicable",
                  "documentation_quality": 80, "integration_feasibility": 84, "auth_clarity": 70,
                  "pricing_transparency": 60, "pricing_notes": "Per-seat tiers are published.",
                  "setup_complexity": 2, "reason": "Documented SDK.", "evidence": ["A worked example."]},
        "beta": {"rating": 55, "implementable": True, "display_name": "Beta",
                 "assessment_basis": "documentation_evidence", "verification_status": "not_applicable",
                 "documentation_quality": 60, "integration_feasibility": 55, "auth_clarity": 50,
                 "pricing_transparency": None, "pricing_notes": "",
                 "setup_complexity": 3, "reason": "Sparse docs.", "evidence": []},
    }
    markdown = write_assessment_report(metrics, [], str(tmp_path / "r.md"))

    assert "| Auth | Pricing | Setup |" in markdown
    assert "Pricing: Per-seat tiers are published." in markdown
    # Beta disclosed no pricing, so the cell is withheld rather than printed as 0.
    beta_row = next(line for line in markdown.splitlines() if "| Beta |" in line)
    assert "| n/a |" in beta_row
    assert "| 0 |" not in beta_row


def test_report_lists_excluded_candidates_without_scoring_them(tmp_path):
    metrics = {"alpha": {"rating": 70, "implementable": True, "display_name": "Alpha",
                         "assessment_basis": "documentation_evidence",
                         "documentation_quality": 70, "integration_feasibility": 70,
                         "auth_clarity": 70, "pricing_transparency": None,
                         "reason": "Fine.", "evidence": []}}
    markdown = write_assessment_report(
        metrics, [], str(tmp_path / "r.md"),
        excluded=[{"name": "gamma", "display_name": "Gamma Cloud",
                   "violates": "Requires a hosted deployment; the stated constraint is on-prem."}],
    )

    assert "## Considered and excluded" in markdown
    assert "**Gamma Cloud**" in markdown
    assert "the stated constraint is on-prem" in markdown
    assert "They were not scored." in markdown
    # It is listed, not ranked: no row in the ranked table belongs to it.
    assert "| Gamma Cloud |" not in markdown


def test_report_omits_the_excluded_section_when_nothing_was_dropped(tmp_path):
    metrics = {"alpha": {"rating": 70, "reason": "Fine.", "evidence": []}}
    markdown = write_assessment_report(metrics, [], str(tmp_path / "r.md"))
    assert "Considered and excluded" not in markdown


# --------------------------------------------------------------- the build path


def test_documented_setup_commands_travel_with_a_runnable_row():
    """A build path may only print setup the documentation actually supports."""
    runnable = result_from_plan(validate_plan(plan()), "passed", True)
    assert runnable["build_commands"] == ["pip install example-sdk"]

    # Validation strips them for anything that will never be built, so a
    # comparison-only row cannot advertise steps nothing documented.
    compared = result_from_plan(
        validate_plan(plan(execution_mode="comparison_only")), "not_applicable", False)
    assert compared["build_commands"] == []
    assert unavailable_result("nothing ran")["build_commands"] == []


def test_report_falls_back_to_the_assessed_parts_when_no_plan_was_produced(tmp_path):
    metrics = {
        "vendor_suite": {"rating": 41, "implementable": False, "display_name": "Vendor Suite",
                         "role": "product", "reason": "The connector is unreleased.",
                         "evidence": []},
        "object_store_sdk": {"rating": 84, "implementable": True, "role": "build_component",
                             "display_name": "Object Store SDK",
                             "build_commands": ["pip install store-sdk", "store init"],
                             "reason": "Documented client and worked examples.", "evidence": []},
        "index_library": {"rating": 66, "implementable": True, "role": "build_component",
                          "display_name": "Index Library", "build_commands": [],
                          "reason": "Documented indexing API.", "evidence": []},
    }
    markdown = write_assessment_report(metrics, [], str(tmp_path / "r.md"))

    assert "## Build path" not in markdown, "the parts list is not a section of its own"
    section = markdown.split("## How to build this yourself")[1].split("\n## ")[0]
    # It says plainly that the design is missing, rather than passing an
    # inventory off as an answer.
    assert "could not be generated on this run" in section
    # Ranked order, with the score each component actually earned.
    assert section.index("**Object Store SDK**") < section.index("**Index Library**")
    assert "**Object Store SDK** — 84/100" in section
    assert "documented setup: `pip install store-sdk; store init`" in section
    # A component with no documented commands says nothing about setup.
    index_line = next(line for line in section.splitlines() if "**Index Library**" in line)
    assert "documented setup" not in index_line
    # Products are not swept into it.
    assert "**Vendor Suite**" not in section


def test_report_has_no_build_section_when_every_candidate_is_a_product(tmp_path):
    metrics = {"alpha": {"rating": 70, "implementable": True, "role": "product",
                         "reason": "Fine.", "evidence": []},
               "beta": {"rating": 50, "implementable": True, "reason": "Fine.", "evidence": []}}
    markdown = write_assessment_report(metrics, [], str(tmp_path / "r.md"))
    assert "Build path" not in markdown
    assert "How to build this yourself" not in markdown


def test_assessment_rows_carry_the_role_the_spec_gave_them(monkeypatch):
    """The verdict reads metrics, not the spec, so the role has to travel."""
    events = []
    run_dir = Path("runs") / f"test_roles_{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(agent, "dispatch_tool", lambda *_a, **_k: json.dumps("official docs"))
    monkeypatch.setattr(
        tool_assessment, "assess_documentation_batch",
        lambda candidates, *_a, **_k: {
            candidate["name"]: {"plan": validate_plan(plan(execution_mode="comparison_only"))}
            for candidate in candidates
        },
    )
    orchestrator = Orchestrator(
        "test-roles", str(run_dir), lambda event, data: events.append((event, data)))
    try:
        metrics = orchestrator.run_benchmark({
            "benchmark_type": "tool_assessment",
            "category": "RAG platforms",
            "objective": "RAG over internal documents",
            "candidates": [
                {"name": "vendor_suite", "display_name": "Vendor Suite",
                 "docs_url": "https://example.com/a", "kind": "saas"},
                {"name": "index_library", "display_name": "Index Library",
                 "docs_url": "https://example.com/b", "kind": "local_tool",
                 "role": "build_component"},
            ],
        })

        assert metrics["vendor_suite"]["role"] == "product"
        assert metrics["index_library"]["role"] == "build_component"
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


def test_assessments_never_use_a_batch_queue(monkeypatch):
    """Latency belongs to a person waiting, so the queue is never used.

    Batch endpoints trade latency for throughput: measured 257s through
    Doubleword's batch queue for a two-candidate assessment against 4.3s through
    concurrent completions on the same provider and model. There is no size at
    which a run should wait in a queue, so there is no switch either.
    """
    used = []

    async def fake_batch(requests, model=None, env=None):
        used.append("batch")
        return []

    async def fake_concurrent(provider, requests, model, env=None):
        used.append(f"concurrent:{provider}")
        return [SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=json.dumps(plan())))]) for _ in requests]

    monkeypatch.setattr(llm_clients, "batch_chat_completions", fake_batch, raising=False)
    monkeypatch.setattr(llm_clients, "_concurrent_chat_completions", fake_concurrent)
    env = {"DOUBLEWORD_API_KEY": "hidden-test-value"}

    for count in (2, 64):
        used.clear()
        assess_documentation_batch(
            [{"name": f"c{i}", "docs_text": "docs"} for i in range(count)],
            "compare integrations", env=env)
        assert used == ["concurrent:doubleword"], f"{count} requests must not be queued"
