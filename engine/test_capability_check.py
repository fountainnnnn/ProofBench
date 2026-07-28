"""A tool that cannot do the job is not implementable, however easy it is to install.

Asked for math question generation *including diagrams*, the assessment rated
`mathgenerator` implementable at 91/100 — a Python library that emits text
problems and no diagrams at all. Its evidence never mentions diagrams; it lists
`pip install`, worked examples, and no auth required.

Nothing was broken in the scoring. `implementable` was defined as "the
documentation is complete enough to build a working integration", which is true
of mathgenerator and says nothing about whether the integration would do what
was asked. The definition now carries a capability test as well, and the run
that exposed this had an empty `constraints.must_have` — so the test has to read
the objective, which is where the requirement actually survived.
"""
from __future__ import annotations

from engine.tool_assessment import _assessment_prompt


def prompt(objective="Generate math practice questions, including diagrams.",
           constraints_text="") -> str:
    return " ".join(_assessment_prompt(
        "mathgenerator", "pip install mathgenerator", objective, [], constraints_text,
    ).split())


def test_capability_is_tested_separately_from_integrability():
    text = prompt()

    assert "CAPABILITY" in text and "INTEGRABILITY" in text
    assert "Set implementable true only when BOTH hold" in text


def test_being_easy_to_install_is_not_evidence_of_capability():
    """The exact wrong inference: pip-installable, therefore implementable."""
    text = prompt()

    assert "being trivially integrable is not evidence of doing the job" in text
    assert "name the missing capability" in text


def test_a_missing_capability_may_not_be_inferred_from_the_category():
    text = prompt()

    assert "Do not infer a capability from the tool's category" in text
    assert "Absent evidence is a fail, not a pass" in text


def test_an_empty_constraints_list_does_not_waive_the_objective():
    """The failing run had constraints.must_have empty; the objective still said
    'including diagrams', and that is what has to be judged."""
    text = prompt(constraints_text="")

    assert "even when the constraints list is empty" in text
    assert "not that it went away" in text


def test_the_objective_still_reaches_the_prompt():
    # The capability test is worthless if the requirement never arrives.
    assert "including diagrams" in prompt()


# ------------------------------------------------------------------ the ordering

from engine.pdf_report import _ranked
from engine.tool_assessment import meets_requirements, rank_key

# The exact inversion measured: capping a failing tool at 49 still beat a
# capable one scoring 43 on thinner documentation.
CAPABLE_BUT_THIN = {"rating": 43, "implementable": True}
INCAPABLE_BUT_POLISHED = {"rating": 49, "implementable": False}


def test_a_capable_tool_outranks_a_higher_scoring_incapable_one():
    assert rank_key(CAPABLE_BUT_THIN) > rank_key(INCAPABLE_BUT_POLISHED)


def test_score_still_orders_within_a_group():
    assert rank_key({"rating": 80, "implementable": True}) > rank_key(CAPABLE_BUT_THIN)
    assert rank_key({"rating": 30, "implementable": False}) < rank_key(INCAPABLE_BUT_POLISHED)


def test_an_absent_flag_is_not_a_demotion():
    """Extraction rows and older assessments carry no implementable field."""
    assert meets_requirements({"rating": 50})
    assert meets_requirements({"rating": 50, "implementable": None})
    assert not meets_requirements({"rating": 50, "implementable": False})


def test_the_pdf_ranks_the_same_way_as_the_report():
    ranked = _ranked({"polished": INCAPABLE_BUT_POLISHED, "capable": CAPABLE_BUT_THIN}, True)
    assert [name for name, _ in ranked] == ["capable", "polished"]


def test_an_unscored_row_still_sorts_last():
    ranked = _ranked({
        "none": {"rating": None},
        "capable": CAPABLE_BUT_THIN,
        "polished": INCAPABLE_BUT_POLISHED,
    }, True)
    assert [name for name, _ in ranked] == ["capable", "polished", "none"]


# ------------------------------------------------ parts are not whole products

def test_a_component_is_judged_on_its_part_not_the_whole_objective():
    """A live run failed every component for not being the whole: SymPy for not
    drawing diagrams, Matplotlib for not writing questions — so the build path
    could never have a viable member and self-implementation silently died."""
    text = " ".join(_assessment_prompt(
        "matplotlib", "docs", "Generate math practice questions with diagrams",
        [], candidate_role="build_component").split())

    assert "BUILD COMPONENT" in text
    assert "AT LEAST ONE" in text
    assert "Never fail a component for not covering the parts" in text


def test_a_product_is_still_held_to_the_whole_objective():
    text = " ".join(_assessment_prompt(
        "acequiz", "docs", "Generate math practice questions with diagrams",
        [], candidate_role="product").split())

    assert "BUILD COMPONENT" not in text
    assert "being trivially integrable is not evidence of doing the job" in text


def test_a_component_contributing_nothing_still_fails():
    """The relaxed bar is 'covers a part', never 'is installable'."""
    text = " ".join(_assessment_prompt(
        "leftpad", "docs", "objective", [], candidate_role="build_component").split())

    assert "contributes to none of the objective's required capabilities" in text


def test_the_part_framing_comes_before_the_objective():
    """Stated after the objective, the role rule lost to it: Nodemailer failed for
    "no webhook support" and Redis, RabbitMQ and Beanstalkd all failed for "no
    scheduled jobs / retry with backoff" — the framework layer's job, not theirs."""
    text = " ".join(_assessment_prompt(
        "nodemailer", "docs", "Send transactional email with webhooks",
        [], candidate_role="build_component").split())

    assert text.index("ONE PART of a self-built integration") < text.index(
        "Send transactional email with webhooks")


def test_the_constraints_block_describes_the_finished_system():
    """The must_have list is what dominated the role rule, so it has to say who
    it is addressed to."""
    text = " ".join(_assessment_prompt(
        "redis", "docs", "Queue background jobs", [],
        constraints_text="Hard requirements: scheduled jobs, retry with backoff",
        candidate_role="build_component").split())

    assert "describe the FINISHED system, not this one part" in text
    assert "must never be failed for lacking a capability another part of the build would supply" in text


def test_a_passing_component_must_name_the_capability_it_covers():
    text = " ".join(_assessment_prompt(
        "sympy", "docs", "objective", [], candidate_role="build_component").split())

    assert "the reason must name which required capability this part covers" in text


def test_the_product_prompt_is_untouched_by_the_component_restructure():
    """Only the component branch moved; products are still judged as wholes."""
    text = _assessment_prompt("acequiz", "docs", "Generate questions", [], "constraints")

    assert text.startswith(
        "Assess 'acequiz' against this company objective: Generate questions.\n"
        "Base every judgement on the supplied documentation. Do not use unstated prior knowledge.\n"
    )
    assert "ONE PART" not in text and "FINISHED system" not in text


def test_the_batch_threads_each_candidates_role(monkeypatch):
    from engine import tool_assessment
    seen = {}

    def spy(tool_name, docs_text, objective, creds, constraints_text="",
            pricing_text="", candidate_role="product", note=""):
        seen[tool_name] = candidate_role
        return {"messages": [{"role": "system", "content": "s"},
                             {"role": "user", "content": "u"}], "temperature": 0}

    monkeypatch.setattr(tool_assessment, "_assessment_request", spy)
    try:
        tool_assessment.assess_documentation_batch(
            [{"name": "product_a", "docs_text": "d", "role": "product"},
             {"name": "part_b", "docs_text": "d", "role": "build_component"}],
            "objective", env={},
        )
    except RuntimeError:
        pass  # no provider configured; only the request building is under test
    assert seen == {"product_a": "product", "part_b": "build_component"}
