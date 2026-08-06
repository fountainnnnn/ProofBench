"""A shortlist that ignores the user's stated constraints is the wrong shortlist.

Intake used to hand every plausible candidate straight to assessment, so a run
that had been told "on-prem only" still spent its budget rating hosted SaaS. The
elimination round fixes that, but it is an advisory gate over model-authored
text: it must drop a candidate only on a stated constraint, record why, and — on
any failure at all — return the shortlist exactly as drafted. These tests pin
both halves, because a gate that could silently lose a run's candidates would
cost far more than it earns.
"""
import json
import types

from engine import agent as agent_mod


def _response(content):
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=content))]
    )


# Two providers so a DISTINCT supervisor resolves: orchestration primary is
# Moonshot (first configured in the orchestration order), and the elimination
# pass is served by DeepSeek — the first configured provider whose identity
# differs. The model that drafted the shortlist never sits in judgement of it.
_DISTINCT_ENV = {"MOONSHOT_API_KEY": "sk-primary", "DEEPSEEK_API_KEY": "sk-reviewer"}


class _Verdict:
    """A stand-in chat client that records every review completion it serves."""

    def __init__(self, content):
        self.content = content
        self.calls = []
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return _response(self.content)


def _make_agent(monkeypatch, verdict, tmp_path, env=None):
    """Wire an Orchestrator to a fake DISTINCT review model.

    The elimination pass runs on the resolved supervisor, reached through
    ``chat_client``; the review never falls back to the orchestrator. ``env``
    defaults to a two-provider deployment where a distinct reviewer exists.
    """
    monkeypatch.setattr(
        agent_mod, "dispatch_tool", lambda name, args, ctx: json.dumps({"ok": True})
    )
    monkeypatch.setattr(
        "engine.llm_clients.chat_client", lambda provider, env=None: verdict)

    emitted = []
    a = agent_mod.Orchestrator(
        run_id="run-review",
        run_dir=str(tmp_path / "run"),
        emit=lambda kind, payload: emitted.append((kind, payload)),
    )
    a.runtime_env = dict(_DISTINCT_ENV if env is None else env)
    a.emitted = emitted
    a.deltas = []
    a.states = []
    a._delta = lambda text: a.deltas.append(text)
    a._state = lambda s: a.states.append(s)
    a._check_cancelled = lambda: None
    return a


def _spec(*names):
    return {
        "benchmark_type": "tool_assessment",
        "category": "RAG platforms",
        "objective": "RAG over internal documents",
        "constraints": {"deployment": "on-prem only"},
        "candidates": [
            {"name": name, "display_name": name.replace("_", " ").title(),
             "docs_url": f"https://example.com/{name}", "kind": "saas"}
            for name in names
        ],
    }


def test_a_stated_constraint_drops_a_candidate_and_says_why(monkeypatch, tmp_path):
    verdict = _Verdict(json.dumps({"drop": [
        {"name": "hosted_only", "violates": "Hosted SaaS only; the stated constraint is on-prem."}
    ]}))
    a = _make_agent(monkeypatch, verdict, tmp_path)

    reviewed = a._review_shortlist(_spec("hosted_only", "self_hosted"))

    assert [c["name"] for c in reviewed["candidates"]] == ["self_hosted"]
    assert reviewed["excluded"] == [{
        "name": "hosted_only",
        "display_name": "Hosted Only",
        "violates": "Hosted SaaS only; the stated constraint is on-prem.",
    }]
    # The user is told, in their own reading, which name left the field and why.
    joined = " ".join(a.deltas)
    assert "Hosted Only" in joined
    assert "on-prem" in joined


def test_an_unparseable_verdict_keeps_the_shortlist_as_drafted(monkeypatch, tmp_path):
    """Advisory gates fail open: a broken reviewer never costs a candidate."""
    verdict = _Verdict("I think you should drop the second one, probably.")
    a = _make_agent(monkeypatch, verdict, tmp_path)
    spec = _spec("alpha", "beta")

    reviewed = a._review_shortlist(spec)

    assert reviewed == spec
    assert "excluded" not in reviewed
    assert a.deltas == []
    assert any(payload.get("tool") == "shortlist_review" and payload.get("status") == "error"
               for _kind, payload in a.emitted)


def test_a_verdict_that_empties_the_field_is_refused_but_never_silent(monkeypatch, tmp_path):
    """Dropping everything is a broken verdict, not a benchmark with no entrants.

    The shortlist is kept — but saying nothing is how a run of four candidates
    that all missed a stated requirement still ended in a report announcing a
    winner. The user is warned before spending the run, not after reading it.
    """
    verdict = _Verdict(json.dumps({"drop": [
        {"name": "alpha", "violates": "Hosted SaaS only; the constraint is on-prem."},
        {"name": "beta", "violates": "No self-hosted tier is documented."},
    ]}))
    a = _make_agent(monkeypatch, verdict, tmp_path)
    spec = _spec("alpha", "beta")

    reviewed = a._review_shortlist(spec)

    # Fail open: the field the user asked for is still the field.
    assert reviewed == spec
    assert "excluded" not in reviewed
    assert [c["name"] for c in reviewed["candidates"]] == ["alpha", "beta"]

    joined = " ".join(a.deltas)
    assert "Warning: every candidate" in joined
    for display_name in ("Alpha", "Beta"):
        assert display_name in joined
    assert "on-prem" in joined and "No self-hosted tier is documented." in joined
    # And the choice stays the user's: run anyway, or name different candidates.
    assert "still run" in joined

    trace = next(payload for _kind, payload in a.emitted
                 if payload.get("tool") == "shortlist_review")
    assert trace["detail"] == "every candidate violates a stated constraint; shortlist kept"
    assert trace["status"] == "ok"


def test_a_single_candidate_shortlist_is_never_reviewed(monkeypatch, tmp_path):
    """There is no field to narrow, so the extra completion is not spent."""
    verdict = _Verdict(json.dumps({"drop": [{"name": "alpha", "violates": "anything"}]}))
    a = _make_agent(monkeypatch, verdict, tmp_path)
    spec = _spec("alpha")

    reviewed = a._review_shortlist(spec)

    assert reviewed == spec
    assert verdict.calls == []


def test_no_distinct_supervisor_skips_the_review_without_self_reviewing(monkeypatch, tmp_path):
    """No distinct reviewer means the shortlist is kept UNREVIEWED — never handed
    back to the model that drafted it. That fallback was correlated self-review;
    the honest outcome is a skip, traced as a healthy optional-review event."""
    def explode(*_a, **_k):
        raise AssertionError("the drafting model must never review its own shortlist")

    monkeypatch.setattr(agent_mod, "_orchestrator_complete", explode)
    verdict = _Verdict(json.dumps({"drop": [{"name": "alpha", "violates": "anything"}]}))
    # One provider: no identity distinct from the primary can be resolved.
    a = _make_agent(monkeypatch, verdict, tmp_path, env={"DEEPSEEK_API_KEY": "sk-only"})
    spec = _spec("alpha", "beta")

    reviewed = a._review_shortlist(spec)

    assert reviewed == spec
    assert "excluded" not in reviewed
    assert verdict.calls == []        # no review model was ever consulted
    assert a.deltas == []
    trace = next(payload for _kind, payload in a.emitted
                 if payload.get("tool") == "shortlist_review")
    assert trace["status"] == "ok"    # a skip is not a pipeline failure
    assert "no distinct supervisor" in trace["detail"]


def test_a_verdict_naming_an_unknown_candidate_is_ignored(monkeypatch, tmp_path):
    """The verdict is model-authored text; a name that was never on the shortlist
    cannot remove one that was."""
    verdict = _Verdict(json.dumps({"drop": [
        {"name": "some_tool_nobody_proposed", "violates": "Hosted only."}
    ]}))
    a = _make_agent(monkeypatch, verdict, tmp_path)
    spec = _spec("alpha", "beta")

    reviewed = a._review_shortlist(spec)

    assert reviewed == spec
    assert [c["name"] for c in reviewed["candidates"]] == ["alpha", "beta"]


def test_an_entry_with_no_named_constraint_cannot_drop_a_candidate(monkeypatch, tmp_path):
    """"Dropped because I said so" is not an audit record."""
    verdict = _Verdict(json.dumps({"drop": [{"name": "alpha", "violates": "  "}]}))
    a = _make_agent(monkeypatch, verdict, tmp_path)
    spec = _spec("alpha", "beta")

    assert a._review_shortlist(spec) == spec


def test_the_review_costs_exactly_one_completion(monkeypatch, tmp_path):
    """One bounded extra call, offered no tools, whatever the shortlist size."""
    verdict = _Verdict(json.dumps({"drop": []}))
    a = _make_agent(monkeypatch, verdict, tmp_path)

    a._review_shortlist(_spec("alpha", "beta", "gamma", "delta"))

    assert len(verdict.calls) == 1
    assert "tools" not in verdict.calls[0]
    # The review sees the stated constraints, or it has nothing to judge against.
    assert "on-prem only" in verdict.calls[0]["messages"][1]["content"]


def test_the_review_is_told_which_candidates_are_build_components(monkeypatch, tmp_path):
    """Without the role, a reviewer drops every component for not being a product."""
    verdict = _Verdict(json.dumps({"drop": []}))
    a = _make_agent(monkeypatch, verdict, tmp_path)
    spec = _spec("alpha", "beta")
    spec["candidates"][1]["role"] = "build_component"

    a._review_shortlist(spec)

    shortlist = json.loads(verdict.calls[0]["messages"][1]["content"])["candidates"]
    assert [c["role"] for c in shortlist] == ["product", "build_component"]
    # And the reviewer is told what to do with one.
    assert "build_component" in verdict.calls[0]["messages"][0]["content"]
    assert "never a violation" in verdict.calls[0]["messages"][0]["content"]


def test_an_extraction_spec_is_not_reviewed(monkeypatch, tmp_path):
    """Only a tool_assessment shortlist has constraints to be narrowed against."""
    verdict = _Verdict(json.dumps({"drop": [{"name": "tesseract", "violates": "anything"}]}))
    a = _make_agent(monkeypatch, verdict, tmp_path)
    spec = {
        "benchmark_type": "extraction",
        "category": "Invoice OCR",
        "fields": ["invoice_number", "date", "vendor", "total"],
        "candidates": [{"name": "tesseract"}, {"name": "easyocr"}],
    }

    assert a._review_shortlist(spec) == spec
    assert verdict.calls == []
