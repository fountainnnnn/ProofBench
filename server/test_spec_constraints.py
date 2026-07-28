"""The run schema is the gate every confirmed spec has to pass.

Intake now records what the user said their environment is, and which candidates
the shortlist review dropped against it. RunRequest re-validates the spec with
extra="forbid", so a field intake writes and the schema does not declare is not a
harmless extra — it is a 422 on POST /run, and the run never starts. These tests
pin the two directions: the new fields are accepted, and nothing else is.
"""
import pytest
from pydantic import ValidationError

from server.schemas import RunRequest


def _spec(**extra):
    spec = {
        "benchmark_type": "tool_assessment",
        "category": "RAG platforms",
        "objective": "RAG over internal documents",
        "candidates": [{"name": "alpha", "display_name": "Alpha",
                        "docs_url": "https://docs.alpha.example/guide", "kind": "saas"}],
    }
    spec.update(extra)
    return spec


def test_constraints_and_exclusions_are_accepted():
    request = RunRequest(spec=_spec(
        constraints={"stack": ["Python", "Postgres"], "must_have": ["SOC 2"],
                     "budget": "under $500/month", "deployment": "on-prem"},
        excluded=[{"name": "gamma", "display_name": "Gamma Cloud",
                   "violates": "Hosted only; the stated constraint is on-prem."}],
    ))

    assert request.spec.constraints.stack == ["Python", "Postgres"]
    assert request.spec.constraints.deployment == "on-prem"
    assert request.spec.excluded[0].name == "gamma"
    assert request.spec.excluded[0].violates.startswith("Hosted only")


def test_both_fields_stay_optional():
    """A spec drafted before this existed, or with nothing to exclude, still runs."""
    request = RunRequest(spec=_spec())
    assert request.spec.constraints is None
    assert request.spec.excluded == []
    assert RunRequest(spec=_spec(constraints={})).spec.constraints.stack == []


def test_an_exclusion_must_name_the_constraint_it_broke():
    """A dropped candidate with no reason is not an audit record."""
    with pytest.raises(ValidationError):
        RunRequest(spec=_spec(excluded=[{"name": "gamma", "violates": ""}]))
    with pytest.raises(ValidationError):
        RunRequest(spec=_spec(excluded=[{"name": "gamma", "display_name": "Gamma"}]))
    # And it must name a candidate slug the rest of the system can key on.
    with pytest.raises(ValidationError):
        RunRequest(spec=_spec(excluded=[{"name": "not a slug!", "violates": "Hosted only."}]))


def test_unknown_constraint_keys_are_refused():
    """extra="forbid" all the way down, so a hallucinated field fails loudly here
    rather than travelling into the assessment prompt unnoticed."""
    with pytest.raises(ValidationError):
        RunRequest(spec=_spec(constraints={"stack": ["Python"], "vibe": "modern"}))


def test_a_build_component_candidate_is_accepted_and_products_are_the_default():
    spec = _spec(candidates=[
        {"name": "alpha", "display_name": "Alpha",
         "docs_url": "https://docs.alpha.example/guide", "kind": "saas"},
        {"name": "beta", "display_name": "Beta", "docs_url": "https://docs.beta.example/sdk",
         "kind": "local_tool", "role": "build_component"},
    ])
    candidates = RunRequest(spec=spec).spec.candidates

    assert candidates[0].role == "product"
    assert candidates[1].role == "build_component"


def test_an_invented_role_is_refused():
    """The build path is claimed with one of two words or not at all."""
    with pytest.raises(ValidationError):
        RunRequest(spec=_spec(candidates=[
            {"name": "alpha", "display_name": "Alpha",
             "docs_url": "https://docs.alpha.example/guide", "kind": "saas",
             "role": "library"}]))


def test_constraint_entries_are_bounded():
    with pytest.raises(ValidationError):
        RunRequest(spec=_spec(constraints={"stack": ["   "]}))
    with pytest.raises(ValidationError):
        RunRequest(spec=_spec(constraints={"must_have": ["x" * 121]}))
    with pytest.raises(ValidationError):
        RunRequest(spec=_spec(constraints={"stack": [f"item-{i}" for i in range(13)]}))
