"""Intake must end with an answer, never with an apology.

A broad question ("what service should I use for a RAG chatbot") sends the agent
searching. Tool calls spend the same loop budget as replies, so a thorough run
used to exhaust the loop mid-research and emit "I'm going in circles â€” please
rephrase", discarding everything it had already found. These tests pin the two
properties that prevent that: the budget survives heavy research, and the final
round is offered no tools so the model has to answer.
"""
import json
import types

import pytest

from engine import agent as agent_mod


def _loop_calls(recorder):
    """The intake loop's own rounds.

    Spec acceptance runs bounded auxiliary passes afterwards — the discovery
    reach gate, the build-path gate and the shortlist review — each one
    completion with its own system prompt and no tools. They are not rounds of
    this loop, and counting them as such makes "the last round withholds tools"
    assert against the wrong call.
    """
    auxiliary = {agent_mod.BUILD_PATH_SYSTEM, agent_mod.SHORTLIST_REVIEW_SYSTEM,
                 agent_mod.DISCOVERY_REACH_SYSTEM, agent_mod.DISCOVERY_HARVEST_SYSTEM}
    return [call for call in recorder.calls
            if (call.get("messages") or [{}])[0].get("content") not in auxiliary]


class _Msg:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []

    def model_dump(self, exclude_none=False):
        return {"role": "assistant", "content": self.content}


def _tool_call(name="web_search", args=None, call_id="c1"):
    return types.SimpleNamespace(
        id=call_id,
        function=types.SimpleNamespace(name=name, arguments=json.dumps(args or {"query": "rag"})),
    )


def _response(msg):
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])


class _Recorder:
    """Stands in for the orchestrator: searches until told it has no tools."""

    def __init__(self, spec_on_toolless=True):
        self.calls = []
        self.spec_on_toolless = spec_on_toolless

    def __call__(self, env=None, **kwargs):
        self.calls.append(kwargs)
        if "tools" in kwargs:
            # Given tools, this model always searches â€” the pathological case.
            return _response(_Msg(tool_calls=[_tool_call(call_id=f"c{len(self.calls)}")]))
        if self.spec_on_toolless:
            # A spec the strict intake schema actually accepts: a category, and a
            # candidate with a real docs URL and kind.
            spec = {
                "benchmark_type": "tool_assessment",
                "category": "RAG platforms",
                "candidates": [
                    {
                        "name": "aws_bedrock",
                        "display_name": "AWS Bedrock",
                        "docs_url": "https://docs.aws.amazon.com/bedrock/",
                        "kind": "hosted_api",
                    }
                ],
            }
            return _response(_Msg(content=f"Here is the spec.\n```json\n{json.dumps(spec)}\n```"))
        return _response(_Msg(content="Which of these matters most to you: cost or latency?"))


def _make_agent(monkeypatch, recorder, tmp_path, dispatch=None, brief=False):
    """Wire an Orchestrator to a fake model and tool layer.

    `dispatch` must be applied here rather than by the caller: this function
    patches dispatch_tool itself, and patching it beforehand was silently
    overwritten — which made a cap test pass without ever dispatching anything.

    `brief` opts into the opening prompt-brief call, which is off by default.
    It runs through the same _orchestrator_complete patch as the intake loop, so
    leaving it live would silently shift every recorded call by one and make the
    round-budget assertions below measure something other than the budget. The
    brief has its own file; these tests are about the loop.
    """
    monkeypatch.setattr(agent_mod, "_orchestrator_complete", recorder)
    monkeypatch.setattr(
        agent_mod,
        "dispatch_tool",
        dispatch or (lambda name, args, ctx: json.dumps({"ok": True, "results": []})),
    )

    emitted = []
    a = agent_mod.Orchestrator(
        run_id="run-1",
        run_dir=str(tmp_path / "run"),
        emit=lambda kind, payload: emitted.append((kind, payload)),
    )
    a.emitted = emitted
    a.deltas = []
    a.states = []
    a._delta = lambda text: a.deltas.append(text)
    a._state = lambda s: a.states.append(s)
    a._check_cancelled = lambda: None
    if not brief:
        a._prepare_brief = lambda _message: ("", None)
    return a


def test_relentless_searching_still_ends_with_a_spec_not_an_apology(monkeypatch, tmp_path):
    recorder = _Recorder(spec_on_toolless=True)
    a = _make_agent(monkeypatch, recorder, tmp_path)

    a.chat("what service for a RAG chatbot")

    joined = " ".join(a.deltas)
    assert "going in circles" not in joined
    assert "rephrase" not in joined
    # The run ends by proposing the benchmark, which is the point of intake.
    assert any(kind == "artifact" and payload.get("kind") == "spec" for kind, payload in a.emitted)
    assert "SPEC_CONFIRM" in a.states


def test_last_round_is_offered_no_tools_so_the_model_must_answer(monkeypatch, tmp_path):
    recorder = _Recorder(spec_on_toolless=True)
    a = _make_agent(monkeypatch, recorder, tmp_path)

    a.chat("what service for a RAG chatbot")

    rounds = _loop_calls(recorder)
    assert "tools" not in rounds[-1], "the final round must withhold tools"
    assert all("tools" in call for call in rounds[:-1]), "earlier rounds keep their tools"


def test_budget_leaves_real_room_for_research(monkeypatch, tmp_path):
    recorder = _Recorder(spec_on_toolless=True)
    a = _make_agent(monkeypatch, recorder, tmp_path)

    a.chat("what service for a RAG chatbot")

    # Every round before the last one was spent searching, and there were enough
    # of them to be worth calling research.
    assert len(_loop_calls(recorder)) >= 10


class _ScrapeLooper:
    """The real failure: every scrape is blocked, so the model keeps retrying.

    This is what produced eighteen calls and no answer — vendor docs routinely
    refuse automated fetches, and nothing stopped the agent from trying the next
    page, and the next.
    """

    def __init__(self):
        self.calls = []

    def __call__(self, env=None, **kwargs):
        self.calls.append(kwargs)
        if "tools" in kwargs:
            return _response(
                _Msg(tool_calls=[_tool_call(name="scrape_docs", args={"url": f"https://v{len(self.calls)}.example.com/docs"}, call_id=f"c{len(self.calls)}")])
            )
        return _response(_Msg(content="Here are my findings so far."))


def test_a_blocked_scrape_is_never_retried_into_the_ground(monkeypatch, tmp_path):
    recorder = _ScrapeLooper()
    attempted = []

    def _always_blocked(name, args, ctx):
        attempted.append(args.get("url"))
        return json.dumps({"error": "documentation retrieval failed: oxylabs HTTPError, direct HTTPError"})

    a = _make_agent(monkeypatch, recorder, tmp_path, dispatch=_always_blocked)

    a.chat("what service for a RAG chatbot")

    # Exactly the budget: it really was tried (so this cannot pass by the tool
    # never being wired up) and then withdrawn, so a blocked vendor cannot
    # consume the turn no matter how many times the model asks for it.
    assert len(attempted) == agent_mod.MAX_INTAKE_SCRAPE_FAILURES, (
        f"scrape dispatched {len(attempted)} times, expected exactly the cap"
    )
    # And the user still gets the findings rather than an apology.
    joined = " ".join(a.deltas)
    assert "findings" in joined
    assert "going in circles" not in joined


def test_a_working_scrape_is_never_throttled(monkeypatch, tmp_path):
    """The cap must key off failures, not off usage."""
    calls = []

    def _always_fine(name, args, ctx):
        calls.append(args.get("url"))
        return json.dumps({"content": "# Docs", "url": args.get("url")})

    recorder = _ScrapeLooper()
    a = _make_agent(monkeypatch, recorder, tmp_path, dispatch=_always_fine)

    a.chat("what service for a RAG chatbot")

    # Every round before the toolless one scraped successfully; nothing was cut off.
    assert len(calls) == agent_mod.INTAKE_ROUNDS - 1


def test_intake_directs_the_agent_at_the_user_s_own_platform():
    """A shortlist that omits the same-vendor answer is the wrong shortlist.

    Asked for a RAG chatbot over SharePoint documents, the agent proposed four
    generic Python frameworks and no same-vendor option. Nothing in the prompt
    told it that the platform the user already runs should drive candidate
    selection, so it optimised for popularity instead of fit.
    """
    prompt = agent_mod.intake_system(dataset_available=False)

    # The existing estate is a selection signal, stated as a rule the model applies
    # to whatever platform it is given.
    assert "first-party" in prompt
    # Selection runs on the user's stated criteria, not on what is popular.
    assert "total cost" in prompt
    assert "Popularity" in prompt
    # Both shapes of answer, so a buyable service is never crowded out by libraries.
    assert "managed services" in prompt and "libraries" in prompt
    # And the search itself has to mention their platform, or it returns listicles.
    assert "listicles" in prompt


def test_intake_names_no_vendors_of_its_own():
    """Selection guidance must be a rule, not a cast list.

    Naming example vendors put those brands in the model's context on every
    intake call, including ones about an unrelated stack, which is a thumb on
    the scale the shortlist should not have. The rule has to carry itself.
    """
    prompt = agent_mod.intake_system(dataset_available=True)

    for vendor in ("SharePoint", "Azure", "Bedrock", "Snowflake", "Salesforce",
                   "Google Workspace", "AWS", "S3"):
        assert vendor not in prompt, f"intake prompt names {vendor}"


def test_extraction_intake_keeps_the_same_platform_guidance():
    """The extraction variant is built from the base prompt and must not drop it."""
    prompt = agent_mod.intake_system(dataset_available=True)
    assert "first-party" in prompt
    assert "managed services" in prompt


def test_a_turn_records_what_it_found_for_the_next_one(monkeypatch, tmp_path):
    """Research has to outlive the turn that did it.

    Only messages were durable, so a follow-up rebuilt its context from visible
    text and searched the same ground again — one session spent 19 searches, then
    12 more on the same question, and lost Azure between the two.
    """
    results = json.dumps([
        {"title": "Azure AI Search", "url": "https://learn.microsoft.com/azure/search/"},
        {"title": "AWS Bedrock Knowledge Bases", "url": "https://docs.aws.amazon.com/bedrock/"},
    ])

    class _SearchOnce:
        def __init__(self):
            self.calls = []

        def __call__(self, env=None, **kwargs):
            self.calls.append(kwargs)
            if "tools" in kwargs and len(self.calls) == 1:
                return _response(_Msg(tool_calls=[_tool_call(name="web_search")]))
            return _response(_Msg(content="Here is what I found."))

    recorder = _SearchOnce()
    a = _make_agent(monkeypatch, recorder, tmp_path, dispatch=lambda n, args, ctx: results)

    a.chat("RAG over sharepoint")

    urls = {item["url"] for item in a.findings}
    assert "https://learn.microsoft.com/azure/search/" in urls
    assert "https://docs.aws.amazon.com/bedrock/" in urls


def test_prior_findings_are_replayed_into_the_next_turn(monkeypatch, tmp_path):
    recorder = _Recorder(spec_on_toolless=True)
    a = _make_agent(monkeypatch, recorder, tmp_path)
    a.prior_findings = [
        {"title": "Azure AI Search", "url": "https://learn.microsoft.com/azure/search/"},
    ]

    a.chat("RAG over sharepoint")

    system_prompt = recorder.calls[0]["messages"][0]["content"]
    assert "Azure AI Search" in system_prompt
    assert "https://learn.microsoft.com/azure/search/" in system_prompt
    # And it must say what to do with them, or the model simply searches again.
    assert "instead of" in system_prompt


def test_a_scraped_page_is_not_scraped_again_next_turn(monkeypatch, tmp_path):
    recorder = _ScrapeLooper()
    a = _make_agent(
        monkeypatch, recorder, tmp_path,
        dispatch=lambda n, args, ctx: json.dumps({"content": "# Docs"}),
    )

    a.chat("RAG over sharepoint")

    # Every successful scrape is remembered by its URL.
    assert any(item["url"].startswith("https://v") for item in a.findings)


def test_a_blocked_page_is_not_recorded_as_a_finding(monkeypatch, tmp_path):
    """A failed fetch found nothing, and must not be replayed as if it had."""
    recorder = _ScrapeLooper()
    a = _make_agent(
        monkeypatch, recorder, tmp_path,
        dispatch=lambda n, args, ctx: json.dumps({"error": "documentation retrieval failed"}),
    )

    a.chat("RAG over sharepoint")

    assert a.findings == []


def _assessment_spec(**extra):
    spec = {
        "benchmark_type": "tool_assessment",
        "category": "RAG platforms",
        "objective": "RAG over internal documents",
        "candidates": [{"name": "alpha", "display_name": "Alpha",
                        "docs_url": "https://example.com/docs", "kind": "saas"}],
    }
    spec.update(extra)
    return spec


def test_stated_constraints_travel_with_the_spec(monkeypatch, tmp_path):
    """The constraint object is the audit record of what the shortlist was picked
    against, so it has to survive normalization."""
    from engine.agent import _normalize_intake_spec

    normalized = _normalize_intake_spec(
        _assessment_spec(constraints={
            "stack": ["Python", "Postgres"],
            "must_have": ["SOC 2"],
            "budget": "under $500/month",
            "deployment": "on-prem",
        }),
        dataset_available=False,
    )

    assert normalized["constraints"] == {
        "stack": ["Python", "Postgres"],
        "must_have": ["SOC 2"],
        "budget": "under $500/month",
        "deployment": "on-prem",
    }


def test_constraints_are_bounded_like_every_other_intake_field():
    """Nothing model-authored reaches the server unclamped."""
    from engine.agent import _normalize_intake_spec

    normalized = _normalize_intake_spec(
        _assessment_spec(constraints={
            "stack": [f"item-{i}" for i in range(20)],
            "must_have": ["m" * 400],
            "budget": "b" * 500,
            "deployment": "d" * 500,
        }),
        dataset_available=False,
    )
    constraints = normalized["constraints"]

    assert len(constraints["stack"]) == 12
    assert len(constraints["must_have"][0]) == 120
    assert len(constraints["budget"]) == 300
    assert len(constraints["deployment"]) == 300


def test_absent_or_malformed_constraints_are_empty_never_invented():
    """A constraint the user never stated must not be filled in for them."""
    from engine.agent import _normalize_intake_spec

    assert _normalize_intake_spec(_assessment_spec(), dataset_available=False)["constraints"] == {}
    for junk in ("on-prem only", ["on-prem"], 7, None):
        normalized = _normalize_intake_spec(
            _assessment_spec(constraints=junk), dataset_available=False)
        assert normalized["constraints"] == {}
    # Empty strings and blank list entries state nothing, so they are dropped.
    sparse = _normalize_intake_spec(
        _assessment_spec(constraints={"stack": ["", "  "], "budget": "  "}),
        dataset_available=False,
    )
    assert sparse["constraints"] == {}


def test_a_build_component_role_survives_normalization():
    """The build path is claimed explicitly by the spec, never inferred later."""
    from engine.agent import _normalize_intake_spec

    spec = _assessment_spec(candidates=[
        {"name": "alpha", "display_name": "Alpha",
         "docs_url": "https://example.com/a", "kind": "saas"},
        {"name": "beta", "display_name": "Beta", "docs_url": "https://example.com/b",
         "kind": "local_tool", "role": "BUILD_COMPONENT"},
    ])
    normalized = _normalize_intake_spec(spec, dataset_available=False)

    assert [c["role"] for c in normalized["candidates"]] == ["product", "build_component"]


def test_an_unrecognised_role_is_a_product():
    """Anything the model invents falls back to the option that claims nothing."""
    from engine.agent import _normalize_intake_spec

    for junk in ("library", "", None, 7, ["build_component"]):
        normalized = _normalize_intake_spec(
            _assessment_spec(candidates=[
                {"name": "alpha", "display_name": "Alpha",
                 "docs_url": "https://example.com/a", "kind": "saas", "role": junk}]),
            dataset_available=False,
        )
        assert normalized["candidates"][0]["role"] == "product"


def test_intake_offers_a_build_path_without_deciding_it_is_the_answer():
    """Intake may propose components; only measured scores may recommend building."""
    # Collapsed, because the guidance is wrapped across indented lines.
    prompt = " ".join(agent_mod.intake_system(dataset_available=False).split())

    assert "build_component" in prompt
    # The trigger is evidence about the field, not a preference for building.
    assert "must-have" in prompt
    # It supplements the marketed products rather than replacing the search.
    assert "never replaces searching for them" in prompt
    # And the conclusion is explicitly deferred to the assessment scores.
    assert "the report's verdict concludes self-implementation only when the " \
           "measured scores support it" in prompt


def test_a_genuine_question_is_delivered_rather_than_swallowed(monkeypatch, tmp_path):
    recorder = _Recorder(spec_on_toolless=False)
    a = _make_agent(monkeypatch, recorder, tmp_path)

    a.chat("what service for a RAG chatbot")

    joined = " ".join(a.deltas)
    assert "cost or latency" in joined
    assert "going in circles" not in joined
