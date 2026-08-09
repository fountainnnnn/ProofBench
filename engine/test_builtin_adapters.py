"""Built-in adapters receive exactly their credentials, and nothing else does."""

import gc

import pytest

from engine.agent import TRUSTED_ADAPTER_TOKEN_FIELD, Orchestrator
from engine.builtin_adapters import (
    BUILTIN_ADAPTER_CREDENTIALS,
    SANDBOX_ELIGIBLE_CREDENTIALS,
    BuiltinAdapterUnavailable,
    entitled_credentials,
    is_builtin_adapter,
    load_builtin_candidate,
    missing_credentials,
)
from engine.candidates.base import Candidate

FULL_ENV = {
    "DOUBLEWORD_API_KEY": "hidden-doubleword",
    "DOUBLEWORD_MODEL": "test-model",
    "DOUBLEWORD_BASE_URL": "https://api.doubleword.ai/v1",
    "OPENAI_API_KEY": "hidden-openai",
    "OPENAI_VISION_MODEL": "test-vision",
}


@pytest.fixture(autouse=True)
def _offline_dns(monkeypatch):
    """Resolve every hostname to a public address without touching the network.

    Entitling DOUBLEWORD_BASE_URL validates the URL, and validation resolves the
    host through the real resolver by default. On a machine without DNS (or in a
    sandbox that blocks it) that made these tests fail on connectivity, which is
    exactly the ambient dependency an offline suite must not have. The URL
    policy itself is still exercised; only the lookup is canned.
    """
    import functools
    import socket

    from engine import network_security

    def public(host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(
        network_security, "validate_external_url",
        functools.partial(network_security.validate_external_url, resolver=public),
    )


def test_registry_declares_only_credentials_its_adapter_source_reads():
    """Every required name must actually appear in the first-party adapter source."""
    for name, (required, optional) in BUILTIN_ADAPTER_CREDENTIALS.items():
        source = load_builtin_candidate(name).adapter_code
        for env_name in (*required, *optional):
            assert env_name in source, f"{name} does not read {env_name}"


def test_sandbox_eligible_set_is_exactly_the_registry_union():
    expected = {
        env_name
        for required, optional in BUILTIN_ADAPTER_CREDENTIALS.values()
        for env_name in (*required, *optional)
    }
    assert set(SANDBOX_ELIGIBLE_CREDENTIALS) == expected
    # Orchestration-only secrets must never be sandbox eligible.
    for forbidden in ("DAYTONA_API_KEY", "DEEPSEEK_API_KEY",
                      "OXYLABS_USERNAME", "OXYLABS_PASSWORD", "MOONSHOT_API_KEY"):
        assert forbidden not in SANDBOX_ELIGIBLE_CREDENTIALS


def test_local_builtins_need_no_credentials():
    for name in ("tesseract", "easyocr", "paddleocr"):
        assert missing_credentials(name, {}) == ()
        assert entitled_credentials(name, {}) == ()


def test_paddleocr_builtin_installs_runtime_and_uses_supported_pipeline_api():
    candidate = load_builtin_candidate("paddleocr")
    assert any(command == "python -m pip install paddlepaddle==3.2.0"
               for command in candidate.build_commands)
    assert any("paddleocr" in command for command in candidate.build_commands)
    assert "PaddleOCR(" in candidate.adapter_code
    assert ".predict(image_path)" in candidate.adapter_code
    assert "rec_texts" in candidate.adapter_code
    # Its models are fetched at build time, so the first inference of a run does
    # not pay for the download.
    assert any("PaddleOCR(" in command for command in candidate.build_commands)


def test_easyocr_installs_pinned_cuda_torch_before_its_runtime_dependencies():
    """Torch comes from an explicit index, and the adapter uses the GPU it finds.

    The index is pinned either way: PyPI's default resolution is what used to
    drag the wrong multi-gigabyte wheels into a sandbox. It now points at CUDA
    because the sandbox carries a GPU, and the adapter detects the device at
    runtime rather than hardcoding one, so a CPU-only sandbox still works.
    """
    candidate = load_builtin_candidate("easyocr")

    assert "download.pytorch.org/whl/cu124" in candidate.build_commands[0]
    assert "torch torchvision" in candidate.build_commands[0]
    assert "easyocr" in candidate.build_commands[1]
    assert "torch.cuda.is_available()" in candidate.adapter_code
    assert "gpu=_use_gpu" in candidate.adapter_code
    assert "_READER" in candidate.adapter_code
    # Weights are still fetched at build time, never inside a run.
    assert any("easyocr.Reader" in command for command in candidate.build_commands)


def test_missing_required_credential_is_explicit_not_a_silent_fallback():
    with pytest.raises(BuiltinAdapterUnavailable) as raised:
        entitled_credentials("doubleword", {"DOUBLEWORD_MODEL": "test-model"})
    assert raised.value.missing == ("DOUBLEWORD_API_KEY",)
    assert "DOUBLEWORD_API_KEY" in str(raised.value)
    # The message names variables, never values.
    assert "hidden" not in str(raised.value)


def test_generated_candidate_borrowing_a_builtin_name_is_not_a_builtin():
    assert is_builtin_adapter("doubleword")
    assert not is_builtin_adapter("doubleword_pro")
    assert not is_builtin_adapter("my-doubleword")


def _orchestrator(tmp_path, env=None):
    return Orchestrator(
        "builtin", str(tmp_path), lambda _event, _data: None,
        provider_env=dict(env if env is not None else FULL_ENV),
    )


def test_trusted_builtin_receives_only_its_own_credentials(tmp_path, monkeypatch):
    orchestrator = _orchestrator(tmp_path)
    builtin = load_builtin_candidate("doubleword")
    token = orchestrator.register_trusted_candidate(
        builtin, entitled_credentials("doubleword", FULL_ENV))

    def implementation(_spec):
        adapter = orchestrator.ctx.candidates["doubleword"]
        code = orchestrator._adapter_code(adapter, "images/inv_001.png")
        assert "hidden-doubleword" in code
        # A different built-in's key is not this candidate's business.
        assert "hidden-openai" not in code
        return {"ok": True}

    monkeypatch.setattr(orchestrator, "_run_tool_assessment_impl", implementation)
    orchestrator.run_tool_assessment({"candidates": [
        {"name": "doubleword", TRUSTED_ADAPTER_TOKEN_FIELD: token}]})


def test_name_spoofed_candidate_receives_no_credentials(tmp_path, monkeypatch):
    """Generated code that calls itself `doubleword` gets nothing without a capability."""
    orchestrator = _orchestrator(tmp_path)
    spoof = Candidate(
        "doubleword", "Not Doubleword", "", "hosted_api", [],
        'import os\nprint(os.environ["DOUBLEWORD_API_KEY"])',
    )

    def implementation(_spec):
        code = orchestrator._adapter_code(spoof, "images/inv_001.png")
        assert "hidden-doubleword" not in code
        return {"ok": True}

    monkeypatch.setattr(orchestrator, "_run_tool_assessment_impl", implementation)
    orchestrator.run_tool_assessment({"candidates": [{"name": "doubleword"}]})


def test_repaired_adapter_loses_its_credential_entitlement(tmp_path, monkeypatch):
    """Once DeepSeek rewrites the code it is no longer first-party source."""
    orchestrator = _orchestrator(tmp_path)
    builtin = load_builtin_candidate("doubleword")
    token = orchestrator.register_trusted_candidate(
        builtin, entitled_credentials("doubleword", FULL_ENV))

    monkeypatch.setattr(
        "engine.adapter_gen.repair_adapter",
        lambda *_args, **_kwargs: 'import os\nprint(os.environ["DOUBLEWORD_API_KEY"])',
    )

    def implementation(_spec):
        adapter = orchestrator.ctx.candidates["doubleword"]
        assert "hidden-doubleword" in orchestrator._adapter_code(adapter, "a.png")
        orchestrator.runtime_env["DEEPSEEK_API_KEY"] = "hidden-deepseek"
        repaired = orchestrator._repair_once(adapter, "boom")
        assert repaired is not None
        adapter.adapter_code = repaired
        assert "hidden-doubleword" not in orchestrator._adapter_code(adapter, "a.png")
        return {"ok": True}

    monkeypatch.setattr(orchestrator, "_run_tool_assessment_impl", implementation)
    orchestrator.run_tool_assessment({"candidates": [
        {"name": "doubleword", TRUSTED_ADAPTER_TOKEN_FIELD: token}]})


def test_overwriting_a_trusted_adapter_revokes_it_and_taints_no_successor(
    tmp_path, monkeypatch
):
    """A displaced trusted adapter must not leak credentials to a recycled id().

    Entitlements are keyed by id(candidate). If a trusted Candidate is dropped
    from ctx.candidates without revocation, CPython may reuse its address for a
    later model-generated Candidate, which would silently inherit the trusted
    object's credentials. Exercised through the real generate_adapter tool path.
    """
    from engine.tools import dispatch_tool

    orchestrator = _orchestrator(tmp_path)
    token = orchestrator.register_trusted_candidate(
        load_builtin_candidate("doubleword"), entitled_credentials("doubleword", FULL_ENV))

    generated_serial = iter(range(1, 1000))

    def fake_generate(tool_name, _docs, env=None, fields=None):
        return Candidate(
            tool_name, f"Generated {next(generated_serial)}", "", "hosted_api", [],
            'import os\nprint(os.environ.get("DOUBLEWORD_API_KEY", ""))',
        )

    monkeypatch.setattr("engine.adapter_gen.generate_adapter", fake_generate)

    def implementation(_spec):
        trusted = orchestrator.ctx.candidates["doubleword"]
        # Baseline: the genuine trusted adapter does receive its own credential.
        assert "hidden-doubleword" in orchestrator._adapter_code(trusted, "a.png")

        # Overwrite it through the tool the model actually drives.
        args = {"tool_name": "doubleword", "docs_md": "# docs"}
        dispatch_tool("generate_adapter", args, orchestrator.ctx)
        first = orchestrator.ctx.candidates["doubleword"]
        assert first is not trusted
        assert "hidden-doubleword" not in orchestrator._adapter_code(first, "a.png")

        # The displaced object lost its binding rather than merely being dropped.
        assert orchestrator._entitlements_for(trusted) == frozenset()

        # Churn the slot repeatedly and force collection, which is what would
        # free the trusted address for reuse. No successor may inherit anything.
        successors = []
        for _ in range(40):
            dispatch_tool("generate_adapter", args, orchestrator.ctx)
            successors.append(orchestrator.ctx.candidates["doubleword"])
            gc.collect()
        for successor in successors:
            assert orchestrator._entitlements_for(successor) == frozenset()
            assert "hidden-doubleword" not in orchestrator._adapter_code(successor, "a.png")

        # Only the still-registered trusted object could ever hold a binding,
        # and it no longer does, so the entitlement table is empty.
        assert orchestrator._adapter_entitlements == {}
        return {"ok": True}

    monkeypatch.setattr(orchestrator, "_run_tool_assessment_impl", implementation)
    orchestrator.run_tool_assessment({"candidates": [
        {"name": "doubleword", TRUSTED_ADAPTER_TOKEN_FIELD: token}]})


def test_entitlement_survives_an_unrelated_candidate_being_replaced(tmp_path, monkeypatch):
    """Revocation is identity-scoped: replacing one slot must not disarm another."""
    from engine.tools import dispatch_tool

    orchestrator = _orchestrator(tmp_path)
    token = orchestrator.register_trusted_candidate(
        load_builtin_candidate("doubleword"), entitled_credentials("doubleword", FULL_ENV))

    monkeypatch.setattr(
        "engine.adapter_gen.generate_adapter",
        lambda tool_name, _docs, env=None: Candidate(
            tool_name, "Other", "", "hosted_api", [], "def extract(_p): return {}"),
    )

    def implementation(_spec):
        trusted = orchestrator.ctx.candidates["doubleword"]
        dispatch_tool("generate_adapter", {"tool_name": "other", "docs_md": "# d"},
                      orchestrator.ctx)
        gc.collect()
        assert "hidden-doubleword" in orchestrator._adapter_code(trusted, "a.png")
        return {"ok": True}

    monkeypatch.setattr(orchestrator, "_run_tool_assessment_impl", implementation)
    orchestrator.run_tool_assessment({"candidates": [
        {"name": "doubleword", TRUSTED_ADAPTER_TOKEN_FIELD: token}, {"name": "other"}]})


def test_entitlement_rejects_a_credential_the_registry_does_not_authorize(tmp_path):
    orchestrator = _orchestrator(tmp_path, env={**FULL_ENV, "DAYTONA_API_KEY": "hidden-daytona"})
    with pytest.raises(ValueError, match="orchestration credentials"):
        orchestrator.register_trusted_candidate(
            load_builtin_candidate("doubleword"), ["DAYTONA_API_KEY"])
