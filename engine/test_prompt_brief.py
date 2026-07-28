"""The opening request, restated as a brief — amplified, never extended.

A brief is prepended to the intake system prompt, so anything it says reads to
the next model as though the user had said it. That makes invention the whole
risk of this feature: a brief that fills in a budget nobody mentioned sends the
turn researching a constraint the user never has. These tests pin the two
properties that contain it — nothing enters the brief that was not in the
request, and a brief that cannot be trusted is dropped entirely rather than
half-used.
"""
import json
import types

import pytest

from engine import agent as agent_mod
from engine.agent import PROMPT_BRIEF_SYSTEM, _build_prompt_brief

BRIEF = {
    "category": "RAG platforms",
    "objective": "Answer questions over internal documents",
    "constraints": {"stack": ["Python"], "must_have": ["on-prem"],
                    "budget": "", "deployment": "self-hosted"},
    "inferred_context": [
        {"assumption": "Internal staff are the users, not customers",
         "basis": "\"our internal docs\""},
    ],
    "unknowns": ["document volume", "team size"],
    "search_angles": ["self-hosted retrieval frameworks for Python"],
    "improved_prompt": ("Find a self-hosted retrieval platform that answers staff "
                        "questions over internal documents on Python infrastructure."),
    "complete": True,
}


def _vague(**overrides):
    """A brief the model judged incomplete, which is what opens the gate."""
    return json.dumps({**BRIEF, "complete": False, **overrides})


def _response(content):
    """A plain assistant reply, shaped like the provider's own message object."""
    message = types.SimpleNamespace(
        content=content,
        tool_calls=[],
        model_dump=lambda **_kwargs: {"role": "assistant", "content": content},
    )
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])


# ------------------------------------------------------------------ the parser


def test_a_valid_brief_parses_into_bounded_fields():
    parsed = _build_prompt_brief(json.dumps(BRIEF))

    assert parsed["category"] == "RAG platforms"
    assert parsed["objective"] == "Answer questions over internal documents"
    assert parsed["constraints"] == {"stack": ["Python"], "must_have": ["on-prem"],
                                     "deployment": "self-hosted"}
    assert parsed["unknowns"] == ["document volume", "team size"]
    assert parsed["search_angles"] == ["self-hosted retrieval frameworks for Python"]
    assert parsed["inferred_context"] == [
        {"assumption": "Internal staff are the users, not customers",
         "basis": '"our internal docs"'},
    ]


def test_a_fenced_reply_is_accepted():
    """Models write ```json blocks whatever the instruction says."""
    parsed = _build_prompt_brief(f"```json\n{json.dumps(BRIEF)}\n```")
    assert parsed["category"] == "RAG platforms"


@pytest.mark.parametrize("content", [
    "",
    None,
    "I think you want a RAG platform.",
    "[]",
    '"a string"',
    "{",
    json.dumps({"category": "RAG"}),
    json.dumps({k: v for k, v in BRIEF.items() if k != "search_angles"}),
    json.dumps({k: v for k, v in BRIEF.items() if k != "inferred_context"}),
    json.dumps({k: v for k, v in BRIEF.items() if k != "improved_prompt"}),
    json.dumps({k: v for k, v in BRIEF.items() if k != "complete"}),
])
def test_anything_malformed_is_dropped_rather_than_half_used(content):
    """None is the honest answer; the turn proceeds on the request as written."""
    assert _build_prompt_brief(content) is None


def test_a_brief_naming_neither_category_nor_objective_is_nothing_at_all():
    empty = {"category": "  ", "objective": "", "constraints": {},
             "inferred_context": [], "unknowns": ["budget"], "search_angles": [],
             "improved_prompt": "", "complete": False}
    assert _build_prompt_brief(json.dumps(empty)) is None


def test_completeness_must_be_an_actual_boolean():
    """A truthy string would silently skip the confirmation the user is owed."""
    for truthy in ("yes", "true", 1, [1]):
        assert _build_prompt_brief(json.dumps({**BRIEF, "complete": truthy}))["complete"] is False
    assert _build_prompt_brief(json.dumps(BRIEF))["complete"] is True


def test_an_inference_without_its_basis_is_dropped():
    """Ungrounded, an inference is indistinguishable from an invention."""
    parsed = _build_prompt_brief(json.dumps({**BRIEF, "inferred_context": [
        {"assumption": "They run Kubernetes", "basis": ""},
        {"assumption": "", "basis": "\"our cluster\""},
        {"assumption": "This is a rewrite of an existing system", "basis": "\"replace\""},
        "not even a dict",
    ]}))

    assert parsed["inferred_context"] == [
        {"assumption": "This is a rewrite of an existing system", "basis": '"replace"'},
    ]


def test_every_field_is_bounded():
    """Model-authored text reaches a system prompt, so nothing arrives unclamped."""
    parsed = _build_prompt_brief(json.dumps({
        "category": "c" * 400,
        "objective": "o" * 4000,
        "constraints": {"stack": [f"item-{i}" for i in range(30)],
                        "must_have": ["m" * 400], "budget": "b" * 900,
                        "deployment": ""},
        "inferred_context": [{"assumption": "a" * 400, "basis": "b" * 400}
                             for _ in range(12)],
        "unknowns": [f"unknown-{i}" for i in range(20)] + ["u" * 200],
        "search_angles": [f"angle-{i}" for i in range(12)],
        "improved_prompt": "p" * 2000,
        "complete": False,
    }))

    assert len(parsed["category"]) == 128
    assert len(parsed["objective"]) == 1000
    assert len(parsed["constraints"]["stack"]) == 12
    assert len(parsed["constraints"]["must_have"][0]) == 120
    assert len(parsed["constraints"]["budget"]) == 300
    assert len(parsed["unknowns"]) == 6
    assert all(len(item) <= 80 for item in parsed["unknowns"])
    assert len(parsed["search_angles"]) == 4
    assert all(len(item) <= 160 for item in parsed["search_angles"])
    assert len(parsed["inferred_context"]) == 5
    assert all(len(item["assumption"]) == 160 and len(item["basis"]) == 160
               for item in parsed["inferred_context"])
    assert len(parsed["improved_prompt"]) == 600


def test_blank_entries_are_dropped_not_carried_as_empty_bullets():
    parsed = _build_prompt_brief(json.dumps({
        **BRIEF, "unknowns": ["", "  ", "budget"], "search_angles": ["  "],
    }))
    assert parsed["unknowns"] == ["budget"]
    assert parsed["search_angles"] == []


# ------------------------------------------------------------------ the prompt


def test_the_brief_prompt_forbids_invention():
    assert "never invent" in PROMPT_BRIEF_SYSTEM.casefold()


def test_the_brief_prompt_carries_no_worked_examples():
    """An example in a system prompt is a standing prior on every session.

    Vendor examples were removed from the intake prompt for exactly this reason;
    a sample category or a sample unknown is the same mistake in a smaller coat.
    """
    prompt = PROMPT_BRIEF_SYSTEM.casefold()

    for vendor in ("sharepoint", "azure", "bedrock", "snowflake", "salesforce",
                   "google workspace", "aws", "s3"):
        assert vendor not in prompt, f"brief prompt names {vendor}"
    assert "e.g." not in prompt
    assert "for example" not in prompt


# -------------------------------------------------------------- the turn itself


class _BriefThenIntake:
    """Answers the brief call first, then behaves like ordinary intake."""

    def __init__(self, brief_content=None):
        self.calls = []
        self.brief_content = json.dumps(BRIEF) if brief_content is None else brief_content

    def __call__(self, env=None, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            return _response(self.brief_content)
        return _response("Which matters more to you: cost or latency?")


def _agent(monkeypatch, recorder, tmp_path):
    monkeypatch.setattr(agent_mod, "_orchestrator_complete", recorder)
    monkeypatch.setattr(agent_mod, "dispatch_tool",
                        lambda name, args, ctx: json.dumps({"ok": True, "results": []}))
    emitted = []
    a = agent_mod.Orchestrator(
        run_id="run-brief", run_dir=str(tmp_path / "run"),
        emit=lambda kind, payload: emitted.append((kind, payload)),
    )
    a.emitted = emitted
    a.deltas = []
    a._delta = lambda text: a.deltas.append(text)
    a._state = lambda _s: None
    a._check_cancelled = lambda: None
    return a


def _traces(agent_obj, tool="prompt_brief"):
    return [payload for _kind, payload in agent_obj.emitted if payload.get("tool") == tool]


def test_the_first_turn_briefs_and_the_block_reaches_the_intake_prompt(monkeypatch, tmp_path):
    recorder = _BriefThenIntake()
    a = _agent(monkeypatch, recorder, tmp_path)

    a.chat("something for RAG over our internal docs")

    # The brief call itself is toolless and asks only for the restatement.
    assert recorder.calls[0]["messages"][0]["content"] == PROMPT_BRIEF_SYSTEM
    assert "tools" not in recorder.calls[0]
    assert recorder.calls[0]["temperature"] == 0

    system_prompt = recorder.calls[1]["messages"][0]["content"]
    assert "restated as a research brief" in system_prompt
    assert "remains authoritative" in system_prompt
    assert "Category: RAG platforms" in system_prompt
    assert "Objective: Answer questions over internal documents" in system_prompt
    assert "Existing stack: Python" in system_prompt
    # Unknowns are named as unknowns, with what to do about them.
    assert "document volume, team size" in system_prompt
    assert "do not assume" in system_prompt
    assert "self-hosted retrieval frameworks for Python" in system_prompt

    # Inferences are declared with their grounding, and the agent is told to put
    # them in front of the user rather than act on them quietly.
    assert "so the user can correct them" in system_prompt
    assert ("- Internal staff are the users, not customers "
            '(from: "our internal docs")') in system_prompt
    # And they must never harden into the spec's audit record.
    assert ("Do not copy assumptions into the spec's constraints") in system_prompt


def test_the_brief_does_not_spend_the_intake_round_budget(monkeypatch, tmp_path):
    """It runs before the loop; the user's research budget is untouched."""
    recorder = _BriefThenIntake()
    a = _agent(monkeypatch, recorder, tmp_path)

    a.chat("something for RAG over our internal docs")

    intake_calls = recorder.calls[1:]
    assert len(intake_calls) <= agent_mod.INTAKE_ROUNDS
    # And the user still got their answer.
    assert "cost or latency" in " ".join(a.deltas)


def test_an_empty_section_is_omitted_rather_than_rendered_blank(monkeypatch, tmp_path):
    sparse = {"category": "Email delivery", "objective": "Send transactional email",
              "constraints": {}, "inferred_context": [], "unknowns": [],
              "search_angles": [], "improved_prompt": "", "complete": True}
    recorder = _BriefThenIntake(brief_content=json.dumps(sparse))
    a = _agent(monkeypatch, recorder, tmp_path)

    a.chat("we need to send email")

    system_prompt = recorder.calls[1]["messages"][0]["content"]
    assert "Category: Email delivery" in system_prompt
    # Nothing was stated, so nothing is claimed — not even an empty heading.
    assert "Stated constraints:" not in system_prompt
    assert "Unknown (" not in system_prompt
    assert "Search angles:" not in system_prompt
    assert "Working assumptions" not in system_prompt
    # The one line that is unconditional: an inference must never harden into
    # the spec's audit record, whether or not this brief happened to make any.
    assert "Do not copy assumptions into the spec's constraints" in system_prompt


def test_a_failed_brief_leaves_the_turn_running_on_the_request_as_written(monkeypatch, tmp_path):
    recorder = _BriefThenIntake(brief_content="sorry, I could not do that")
    a = _agent(monkeypatch, recorder, tmp_path)

    a.chat("something for RAG over our internal docs")

    system_prompt = recorder.calls[1]["messages"][0]["content"]
    assert "restated as a research brief" not in system_prompt
    # The turn still happened and still answered.
    assert "cost or latency" in " ".join(a.deltas)
    trace = _traces(a)[0]
    assert trace["status"] == "error"
    assert trace["detail"] == "brief unavailable; proceeding with the request as written"


def test_a_provider_outage_on_the_brief_is_not_fatal(monkeypatch, tmp_path):
    calls = []

    def _explode_then_intake(env=None, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise RuntimeError("provider unreachable")
        return _response("Which matters more to you: cost or latency?")

    a = _agent(monkeypatch, _explode_then_intake, tmp_path)

    a.chat("something for RAG over our internal docs")

    assert "cost or latency" in " ".join(a.deltas)
    assert _traces(a)[0]["status"] == "error"


def test_a_successful_brief_is_traced_with_what_it_found(monkeypatch, tmp_path):
    recorder = _BriefThenIntake()
    a = _agent(monkeypatch, recorder, tmp_path)

    a.chat("something for RAG over our internal docs")

    trace = _traces(a)[0]
    assert trace["status"] == "ok"
    assert trace["args_summary"] == "RAG platforms"
    assert trace["detail"] == "1 stated must-have, 2 unknowns"


# ------------------------------------------------- the confirmation round-trip


def _directions(agent_obj):
    return [payload for _kind, payload in agent_obj.emitted
            if payload.get("kind") == "direction"]


def test_a_vague_request_is_confirmed_before_a_single_search_is_spent(monkeypatch, tmp_path):
    """Searching first and asking later spends the budget on a guessed reading."""
    recorder = _BriefThenIntake(brief_content=_vague())
    a = _agent(monkeypatch, recorder, tmp_path)

    a.chat("something for RAG over our internal docs")

    # The brief call, and nothing else: the intake loop was never entered.
    assert len(recorder.calls) == 1
    direction = _directions(a)[0]
    assert direction["improved_prompt"] == BRIEF["improved_prompt"]
    assert direction["assumptions"] == [
        {"assumption": "Internal staff are the users, not customers",
         "basis": '"our internal docs"'},
    ]
    assert direction["unknowns"] == ["document volume", "team size"]
    # The user is told what to do with it, without being asked a question they
    # have to compose an answer to.
    assert "Confirm or correct the direction above" in " ".join(a.deltas)


def test_the_gated_turn_is_still_remembered(monkeypatch, tmp_path):
    """The confirmation reply must land in a conversation, not in a vacuum."""
    recorder = _BriefThenIntake(brief_content=_vague())
    a = _agent(monkeypatch, recorder, tmp_path)

    a.chat("something for RAG over our internal docs")

    assert a._messages[0]["role"] == "system"
    assert a._messages[-1] == {"role": "user",
                               "content": "something for RAG over our internal docs"}


def test_a_complete_request_is_never_gated(monkeypatch, tmp_path):
    """Confirming what the user already pinned down is a wasted round-trip."""
    recorder = _BriefThenIntake()  # complete=True
    a = _agent(monkeypatch, recorder, tmp_path)

    a.chat("we need on-prem RAG over internal docs on Python, must be self-hosted")

    assert len(recorder.calls) > 1, "the intake loop must have run"
    assert _directions(a) == []


def test_a_failed_brief_never_gates(monkeypatch, tmp_path):
    """No brief means no confirmation to offer; the request runs as written."""
    recorder = _BriefThenIntake(brief_content="sorry, I could not do that")
    a = _agent(monkeypatch, recorder, tmp_path)

    a.chat("something vague")

    assert len(recorder.calls) > 1
    assert _directions(a) == []


def test_a_brief_with_no_improved_prompt_cannot_gate(monkeypatch, tmp_path):
    """The card's whole body is the improved prompt; without one there is no card."""
    recorder = _BriefThenIntake(brief_content=_vague(improved_prompt="   "))
    a = _agent(monkeypatch, recorder, tmp_path)

    a.chat("something vague")

    assert len(recorder.calls) > 1
    assert _directions(a) == []


def test_the_confirmation_reply_is_an_ordinary_turn(monkeypatch, tmp_path):
    """The card sends a normal message, so the second turn neither briefs nor gates."""
    recorder = _BriefThenIntake(brief_content=_vague())
    a = _agent(monkeypatch, recorder, tmp_path)

    a.chat("something for RAG over our internal docs")
    assert len(recorder.calls) == 1
    a.chat(f"Proceed with this direction: {BRIEF['improved_prompt']}")

    follow_up = recorder.calls[1:]
    assert follow_up, "the confirmation must run the intake loop"
    assert all(call["messages"][0]["content"] != PROMPT_BRIEF_SYSTEM for call in follow_up)
    # One card for the session; the confirmed turn goes straight to research.
    assert len(_directions(a)) == 1


def test_a_server_seeded_orchestrator_still_briefs_its_first_turn(monkeypatch, tmp_path):
    """The gate has to survive how the server actually builds an orchestrator.

    Production never hands `chat` a bare object: every request rebuilds the
    orchestrator and seeds `_messages` with the system prompt (plus any restored
    history) before calling it. A first-turn check written as "the list is
    empty" is therefore true only in tests — it made the brief dead code in the
    deployed app while every unit test here passed, which is exactly the failure
    a live request found.
    """
    recorder = _BriefThenIntake(brief_content=_vague())
    a = _agent(monkeypatch, recorder, tmp_path)
    a._messages = [{"role": "system", "content": "seeded by the server"}]

    a.chat("something for math quizzes for our school app")

    assert recorder.calls[0]["messages"][0]["content"] == PROMPT_BRIEF_SYSTEM
    assert _directions(a), "a seeded first turn must still reach the gate"


def test_a_restored_session_with_history_is_not_re_briefed(monkeypatch, tmp_path):
    """Seeded history containing a real user turn is not a first turn."""
    recorder = _BriefThenIntake()
    a = _agent(monkeypatch, recorder, tmp_path)
    a._messages = [
        {"role": "system", "content": "seeded by the server"},
        {"role": "user", "content": "something for RAG over our internal docs"},
        {"role": "assistant", "content": "Which matters more: cost or latency?"},
    ]

    a.chat("cost matters more")

    assert all(call["messages"][0]["content"] != PROMPT_BRIEF_SYSTEM
               for call in recorder.calls)
    assert not _traces(a)


def test_a_follow_up_turn_is_never_re_briefed(monkeypatch, tmp_path):
    """By the second message the conversation itself is the context."""
    recorder = _BriefThenIntake()
    a = _agent(monkeypatch, recorder, tmp_path)

    a.chat("something for RAG over our internal docs")
    first_turn_calls = len(recorder.calls)
    a.chat("what about the cost?")

    follow_up = recorder.calls[first_turn_calls:]
    assert follow_up, "the follow-up turn must still run"
    assert all(call["messages"][0]["content"] != PROMPT_BRIEF_SYSTEM for call in follow_up)
    # Exactly one brief for the session, from the first turn.
    assert len(_traces(a)) == 1
