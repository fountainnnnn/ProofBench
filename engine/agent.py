"""Orchestrator agent (CONTRACTS §9).

Two modes:
- chat(): conversational INTAKE/DISCOVERY — proposes an editable benchmark spec.
- run_benchmark(): autonomous LLM tool-calling loop over the full protocol.
  Falls back to run_benchmark_scripted() (deterministic, same building blocks)
  if the tool loop derails. Real execution failures remain explicit.

The agent decides logistics. It NEVER judges extraction correctness — scoring
happens only in engine.evaluate (deterministic, ground truth based).
"""

from __future__ import annotations

import copy
import ipaddress
import json
import os
import re
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from engine.candidates.base import Candidate, RESULT_JSON_WRAPPER
from engine.sandbox_pool import SandboxPool
from engine.tools import (
    TOOL_SCHEMAS,
    MAX_RESULT_RECORD_BYTES,
    RunContext,
    append_result_record,
    cleanup_run_context,
    dispatch_tool,
    env_prelude,
    redact_data,
    redact_secret_values,
    replace_candidate,
)

KIMI_BASE_URL = "https://api.moonshot.ai/v1"
MAX_TOOL_CALLS = 40
MAX_CHAT_TOOL_RESULT_CHARS = 3_500
FIELDS = ["invoice_number", "date", "vendor", "total"]
CHAT_TOOLS = {"web_search", "scrape_docs"}
_HISTORY_PATH = re.compile(
    r"(?:[A-Za-z]:[\\/](?![\\/])[^\s\"']*|file://[^\s\"']*|/(?:Users|home|root|app|tmp|var|etc|private|workspace)(?:[\\/][^\s\"']*)?)",
    re.IGNORECASE,
)
# The protocol phase each orchestrator tool belongs to (RUN_SYSTEM above), used
# only to report progress while the agent drives the run. Tools that are not
# tied to one phase (web_search, upload_files, record_result) are absent.
TOOL_PHASES = {
    "scrape_docs": "DOCS_INTEL",
    "generate_adapter": "ADAPTER_GEN",
    "spawn_sandbox": "PROVISIONING",
    "exec_in_sandbox": "BUILDING",
    "run_python_in_sandbox": "RUNNING",
    "evaluate": "EVALUATING",
    "write_report": "REPORTING",
}
TRUSTED_ADAPTER_TOKEN_FIELD = "trusted_adapter_token"
NEVER_SANDBOX_PREFIXES = (
    "DAYTONA_",
    "DEEPSEEK_",
    "DOUBLEWORD_",
    "KIMI_",
    "MOONSHOT_",
    "OPENAI_",
    "OPENROUTER_",
    "ORCHESTRATOR_",
    "OXYLABS_",
)


APP_ROOT = Path(__file__).resolve().parent.parent
# The server-owned sample dataset. Deployments point PROOFBENCH_DATASET_ROOT at
# the tenant upload root (/app/data/uploads in the container), and this sits
# beside it rather than inside it, so confinement has to name it explicitly.
# Mirrors the rule server.storage applies when registering the synthetic dataset.
SAMPLE_DATASET_PATH = APP_ROOT / "data" / "demo"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def _resolved_images(dataset) -> list[tuple[str, Path]]:
    """(name, resolved path) for every image genuinely inside <dataset>/images.

    Every child is re-resolved at each use rather than trusted from an earlier
    pass, so an images directory or an image file swapped for a symlink between
    preparing a run and uploading it is rejected instead of followed. Errors
    stay generic on purpose: the caller is not entitled to learn where a
    rejected link pointed.
    """
    if not dataset:
        return []
    try:
        dataset_path = Path(dataset).resolve(strict=True)
        images_dir = (dataset_path / "images").resolve(strict=True)
    except OSError:
        return []
    if not images_dir.is_dir():
        return []
    if dataset_path not in images_dir.parents:
        raise ValueError("dataset images directory is outside the dataset root")

    found: list[tuple[str, Path]] = []
    for entry in sorted(images_dir.iterdir(), key=lambda item: item.name):
        if entry.suffix.casefold() not in IMAGE_SUFFIXES or not entry.is_file():
            continue
        resolved = entry.resolve(strict=True)
        if resolved.parent != images_dir or dataset_path not in resolved.parents:
            raise ValueError("dataset image is outside the dataset root")
        found.append((entry.name, resolved))
    return found


def _dataset_roots(env: dict) -> tuple[Path, Path]:
    """Return (upload root, canonical sample dataset), both fully resolved.

    A relative PROOFBENCH_DATASET_ROOT is anchored to the application root, the
    same way server.storage anchors it — resolving it against the process CWD
    would let the engine and the server disagree about what is confined.
    """
    configured = str(env.get("PROOFBENCH_DATASET_ROOT") or "").strip()
    upload_root = Path(configured) if configured else APP_ROOT / "data" / "uploads"
    if not upload_root.is_absolute():
        upload_root = APP_ROOT / upload_root
    return upload_root.resolve(), SAMPLE_DATASET_PATH.resolve()


class _RunCancelled(RuntimeError):
    """Internal signal that must not be downgraded to a candidate failure."""

INTAKE_SYSTEM = """You are ProofBench's intake agent. Every ProofBench run is a real,
measured execution. The user may want to compare any company tools or services, not
only OCR or document-extraction products.

Your job in this conversation:
1. Understand what category of tools they want to compare.
2. Capture the company's implementation objective and important constraints.
3. If they named specific tools, find each official implementation guide. If not, use
   web_search to find 3-5 strong candidates, then scrape_docs on the most promising
   official documentation pages. Prefer primary vendor docs over reviews.
4. When you have enough, propose the benchmark spec as a fenced ```json block
   with EXACTLY this shape:
   {"benchmark_type": "tool_assessment",
    "category": str,
    "objective": str,
    "candidates": [{"name": slug, "display_name": str, "docs_url": str,
                    "pricing_url": str, "kind": "local_tool"|"hosted_api"|"saas"}]}
5. Every candidate must have a real docs_url from search or scraped evidence. Do not use
   the built-in OCR candidates unless the user explicitly asks for OCR.
6. A tool_assessment rates documented implementation feasibility. It does NOT score a
   labelled dataset, so never describe its output as extraction accuracy.
7. Keep replies concise and concrete. Explain that implementation is attempted only when
   the docs are sufficient; otherwise Daytona is skipped and the tool receives a rating."""

EXTRACTION_INTAKE_SYSTEM = INTAKE_SYSTEM + """

A labelled dataset with ground truth is attached to this session. If the user's objective
is document extraction or OCR — invoices, receipts, forms, scanned documents, reading
fields off images — propose an EXTRACTION benchmark instead, as a fenced ```json block
with EXACTLY this shape:
   {"benchmark_type": "extraction",
    "category": str,
    "fields": ["invoice_number", "date", "vendor", "total"],
    "candidates": [{"name": slug, "docs_url": str, "pricing_url": str,
                    "kind": "local_tool"|"hosted_api", "use_fallback": bool}]}
An extraction benchmark runs each candidate over every labelled document in a Daytona
sandbox and scores the output against ground truth, so prefer it whenever the objective
is extraction and the dataset can answer the question. ProofBench ships first-party
adapters for these candidate names: tesseract, easyocr, paddleocr, doubleword,
openai_vision, nosana_vlm. Set use_fallback=true to use ProofBench's own adapter for one of those
names; the server supplies its credentials. For any other candidate set use_fallback
false and supply real documentation so an adapter can be generated.

Use tool_assessment only when the objective is not extraction, or when no labelled
dataset can settle it."""


SPEC_RETRY_NUDGE = """That request already names the candidates and states an extraction
objective, and a labelled dataset is bound to this session. Propose the extraction
benchmark spec now as a fenced ```json block in the documented shape, using only the
candidates, fields, and objective already established. Reply with a question instead
only if a required part of the spec is genuinely still unknown."""

# Extraction wording in the user's own words; never used to fill the spec.
_EXTRACTION_INTENT = re.compile(
    r"extract|ocr|invoice|receipt|scanned|document|field", re.IGNORECASE
)


def _compact(text: str) -> str:
    """Lowercase alphanumerics only, so "OpenAI Vision" matches openai_vision."""
    return re.sub(r"[^a-z0-9]+", "", str(text or "").casefold())


_INTAKE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_PRICING_PLACEHOLDERS = {"open source", "open-source", "opensource", "n/a", "na", "none", "free"}


def _strip_json_comments_and_trailing_commas(value: str) -> str:
    """Accept common model JSON decoration without touching string contents.

    LLMs routinely include explanatory ``//`` or ``/* */`` comments in an
    otherwise useful fenced JSON specification.  This scanner only removes
    comments while outside a JSON string, so document URLs such as ``https://``
    remain byte-for-byte intact.  A second string-aware pass removes commas
    immediately before a closing object or array.
    """
    output: list[str] = []
    index = 0
    in_string = False
    escaped = False
    while index < len(value):
        char = value[index]
        if in_string:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            output.append(char)
            index += 1
        elif char == "/" and index + 1 < len(value) and value[index + 1] == "/":
            index = value.find("\n", index + 2)
            if index < 0:
                break
            output.append("\n")
            index += 1
        elif char == "/" and index + 1 < len(value) and value[index + 1] == "*":
            end = value.find("*/", index + 2)
            if end < 0:
                return ""
            output.extend("\n" for item in value[index:end + 2] if item == "\n")
            index = end + 2
        else:
            output.append(char)
            index += 1

    cleaned: list[str] = []
    in_string = False
    escaped = False
    for index, char in enumerate(output):
        if in_string:
            cleaned.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            cleaned.append(char)
            continue
        if char == ",":
            next_index = index + 1
            while next_index < len(output) and output[next_index].isspace():
                next_index += 1
            if next_index < len(output) and output[next_index] in "}]":
                continue
        cleaned.append(char)
    return "".join(cleaned)


def _valid_intake_url(value: object) -> bool:
    try:
        parsed = urlsplit(str(value or "").strip())
        host = (parsed.hostname or "").rstrip(".").casefold()
        port = parsed.port
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
        return False
    if host == "localhost" or host.endswith(".localhost") or port is not None and not 1 <= port <= 65535:
        return False
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        return True


def _intake_slug(value: object) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip(".-")
    return slug[:64]


def _normalize_intake_spec(spec: object, dataset_available: bool) -> dict | None:
    """Return only a spec the strict run schema can accept, or no spec at all."""
    if not isinstance(spec, dict) or not isinstance(spec.get("candidates"), list):
        return None
    declared = str(spec.get("benchmark_type") or "").strip()
    if declared not in {"extraction", "tool_assessment"}:
        declared = "extraction" if spec.get("fields") else "tool_assessment"
    if declared == "extraction" and not dataset_available:
        declared = "tool_assessment"

    category = str(spec.get("category") or "").strip()[:128]
    if not category:
        return None
    normalized_candidates: list[dict] = []
    seen: set[str] = set()
    for raw in spec["candidates"][:20]:
        if not isinstance(raw, dict):
            continue
        display_name = str(raw.get("display_name") or raw.get("name") or "").strip()[:160]
        name = _intake_slug(raw.get("name") or display_name)
        if not _INTAKE_NAME_RE.fullmatch(name) or name in seen:
            continue
        docs_url = str(raw.get("docs_url") or "").strip()[:2048]
        pricing_url = str(raw.get("pricing_url") or "").strip()[:2048]
        if pricing_url.casefold() in _PRICING_PLACEHOLDERS or not _valid_intake_url(pricing_url):
            pricing_url = ""
        if declared == "tool_assessment" and not _valid_intake_url(docs_url):
            continue
        if declared == "extraction" and docs_url and not _valid_intake_url(docs_url):
            docs_url = ""
        kind = str(raw.get("kind") or "").strip().casefold()
        if declared == "tool_assessment":
            if kind not in {"local_tool", "hosted_api", "saas"}:
                kind = "hosted_api" if "api" in kind else "saas"
            candidate = {"name": name, "display_name": display_name or name,
                         "docs_url": docs_url, "pricing_url": pricing_url, "kind": kind}
        else:
            if kind not in {"local_tool", "hosted_api"}:
                kind = "hosted_api" if "api" in kind or "saas" in kind else "local_tool"
            candidate = {"name": name, "docs_url": docs_url,
                         "pricing_url": pricing_url, "kind": kind,
                         "use_fallback": bool(raw.get("use_fallback", True))}
        seen.add(name)
        normalized_candidates.append(candidate)
    if not normalized_candidates:
        return None
    if declared == "tool_assessment":
        objective = str(spec.get("objective") or category).strip()[:4000]
        if not objective:
            return None
        return {"benchmark_type": declared, "category": category, "objective": objective,
                "candidates": normalized_candidates}
    fields = []
    for field in spec.get("fields") or []:
        normalized = _intake_slug(field)
        if _INTAKE_NAME_RE.fullmatch(normalized) and normalized not in fields:
            fields.append(normalized)
    # The deterministic evaluator owns this fixed invoice field set. A model
    # cannot relabel the task into a different schema and still call it scored.
    if fields != FIELDS:
        return None
    return {"benchmark_type": declared, "category": category, "fields": fields,
            "candidates": normalized_candidates}


def intake_system(dataset_available: bool) -> str:
    """Intake instructions, widened to extraction when labelled data is bound."""
    return EXTRACTION_INTAKE_SYSTEM if dataset_available else INTAKE_SYSTEM

RUN_SYSTEM = """You are ProofBench's orchestrator agent. Execute this protocol strictly,
one phase at a time, using the provided tools. You manage Daytona sandboxes;
you NEVER judge extraction quality yourself (a deterministic evaluator does).

Protocol:
1. DOCS_INTEL: for each candidate, scrape_docs on its docs_url (skip if already done).
2. ADAPTER_GEN: generate_adapter(name, docs) for each candidate (skip candidates
   marked use_fallback=true — for those just proceed; the engine supplies fallbacks).
3. PROVISIONING: spawn_sandbox(label) once per candidate, label = candidate name.
4. BUILDING: exec_in_sandbox each of the candidate's build commands, in order.
5. VALIDATING: run_python_in_sandbox the validation code I give you per candidate.
6. RUNNING: run_python_in_sandbox the dataset runner code per candidate.
   Per-document results are collated automatically from the output — you do NOT
   call record_result yourself. Runner code MUST print one line per document:
   RESULT_JSON:{"ok": bool, "fields": {...}, "latency_s": float, "doc_id": "inv_001"}
7. EVALUATING: call evaluate(results_path, ground_truth_path) once.
8. REPORTING: call write_report(metrics_json) once, then reply DONE.

Rules: if a build or validation fails, read the error, try ONE fix, then mark the
candidate failed and move on — never block the others. Keep tool args minimal.
When the protocol is complete, reply with exactly DONE."""


def _orchestrator_provider(env: dict | None = None) -> str:
    """Resolve the orchestrator provider from configured capability.

    ORCHESTRATOR_PROVIDER still pins moonshot/kimi, openai, or openrouter.
    Otherwise the first configured provider in preference order wins, so a
    deployment holding only OPENROUTER_API_KEY orchestrates on OpenRouter.
    ``openai`` remains the terminal default so an unconfigured deployment fails
    on the missing OpenAI key exactly as it did before.
    """
    from engine.llm_clients import capability_providers

    env = os.environ if env is None else env
    configured = capability_providers("orchestration", env)
    return configured[0] if configured else "openai"


def _orchestrator_client(env: dict | None = None):
    from engine.llm_clients import PROVIDERS, chat_client

    env = os.environ if env is None else env
    provider = _orchestrator_provider(env)
    # Preserve the historical KeyError on a deployment with no LLM key at all.
    api_key_env = PROVIDERS[provider].api_key_env
    if not str(env.get(api_key_env) or "").strip():
        raise KeyError(api_key_env)
    return chat_client(provider, env)


def _orchestrator_model(env: dict | None = None) -> str:
    from engine.llm_clients import provider_model

    env = os.environ if env is None else env
    return provider_model(_orchestrator_provider(env), env)


class Orchestrator:
    def __init__(
        self,
        run_id: str,
        run_dir: str,
        emit,
        cancel_event=None,
        provider_env=None,
        dataset_available: bool = False,
    ):
        self.run_id = run_id
        self.run_dir = run_dir
        self.emit = emit
        self.cancel_event = cancel_event
        # Set by the server when the session has a labelled dataset bound, which
        # is what makes a scored extraction benchmark possible at intake.
        self.dataset_available = bool(dataset_available)
        os.makedirs(run_dir, exist_ok=True)
        self.results_path = os.path.join(run_dir, "results.jsonl")
        self.provider_env = dict(provider_env or {})
        self.runtime_env = dict(os.environ)
        self.runtime_env.update(self.provider_env)
        self._registered_candidates: dict[str, Candidate] = {}
        self._trusted_adapter_registry: dict[
            str, tuple[Candidate, frozenset[str]]
        ] = {}
        # id(candidate) -> (candidate, credentials). The Candidate is held by
        # strong reference on purpose: it keeps the id() reserved for as long as
        # the binding exists, so a freed object's address can never be recycled
        # into an entitlement it was not granted. Every read re-checks identity.
        self._adapter_entitlements: dict[int, tuple[Candidate, frozenset[str]]] = {}
        self._trusted_candidate_names: set[str] = set()
        self._attempt_started = False
        self.pool = SandboxPool(size=4, owner_key=run_id)
        self.ctx = RunContext(
            run_id=run_id,
            run_dir=run_dir,
            pool=self.pool,
            emit=emit,
            results_path=self.results_path,
            runtime_env=dict(self.runtime_env),
            revoke_entitlements=self._revoke_adapter_credentials,
        )
        self.ctx.env_passthrough = self.provider_env or {
            k: os.environ[k]
            for k in ("NOSANA_BASE_URL", "NOSANA_API_KEY", "NOSANA_MODEL", "DOUBLEWORD_BASE_URL", "DOUBLEWORD_API_KEY", "DOUBLEWORD_MODEL", "OPENAI_API_KEY", "OPENAI_VISION_MODEL")
            if os.environ.get(k)
        }
        self._run_lock = threading.Lock()
        self._handle_to_candidate: dict[str, str] = {}
        self._messages: list[dict] = []
        self.artifact_warnings: list[dict[str, str]] = []

    def _secrets(self) -> tuple[str, ...]:
        secret_name = re.compile(r"(?i)(key|token|pass|secret)")
        return tuple(
            sorted(
                {
                    str(value)
                    for key, value in self.ctx.env_passthrough.items()
                    if value and secret_name.search(str(key))
                },
                key=len,
                reverse=True,
            )
        )

    def _redact(self, value) -> str:
        return redact_secret_values(value, self._secrets())

    def _redact_data(self, value):
        return redact_data(value, self._secrets())

    def _history_url(self, value) -> str:
        """Keep a public citation identity without passing credentials or paths."""
        try:
            parsed = urlsplit(str(value or ""))
        except ValueError:
            return ""
        host = (parsed.hostname or "").casefold()
        if parsed.scheme not in {"http", "https"} or not host:
            return ""
        if host in {"localhost", "::1"} or host.startswith("127."):
            return ""
        # Rebuild the authority from host/port so credential-bearing userinfo
        # can never be copied into the provider transcript.
        authority = f"[{host}]" if ":" in host else host
        try:
            if parsed.port is not None:
                authority = f"{authority}:{parsed.port}"
        except ValueError:
            return ""
        # Query strings often carry signed or credential-bearing values. The
        # canonical document path remains sufficient for the next LLM turn.
        return urlunsplit((parsed.scheme, authority, parsed.path, "", ""))

    def _tool_result_for_history(self, name: str, args: dict, result) -> str:
        """Produce a compact, secret-free evidence record for an LLM turn.

        Tool output can contain a whole scraped page. Keeping it verbatim in
        the conversation made several normal discovery calls exceed provider
        context before the agent could propose a spec. This affects only the
        provider transcript, never the durable trace or the citation ledger.
        """
        safe = self._redact(result)
        safe = _HISTORY_PATH.sub("[redacted-path]", safe)
        safe = re.sub(r"\s+", " ", safe).strip()
        clipped = safe[:MAX_CHAT_TOOL_RESULT_CHARS]
        payload = {
            "tool": str(name),
            "result_excerpt": clipped,
            "truncated": len(safe) > len(clipped),
        }
        citation_url = self._history_url((args or {}).get("url"))
        if citation_url:
            payload["citation_url"] = citation_url
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    def register_candidate(self, candidate: Candidate) -> None:
        """Register a trusted offline adapter template for future attempts."""
        if not isinstance(candidate, Candidate) or not candidate.name:
            raise ValueError("registered candidate must be a named Candidate")
        self._registered_candidates[candidate.name] = copy.deepcopy(candidate)

    def _validated_adapter_credentials(self, names) -> frozenset[str]:
        from engine.network_security import validate_external_url

        if isinstance(names, (str, bytes)) or names is None:
            raise ValueError("credential names must be an explicit collection")
        from engine.builtin_adapters import SANDBOX_ELIGIBLE_CREDENTIALS

        allowed: set[str] = set()
        for name in names:
            env_name = str(name)
            canonical_name = env_name.upper()
            # Orchestration credentials never reach a sandbox. The only
            # exceptions are the exact names a first-party adapter genuinely
            # needs to run, enumerated server-side in engine.builtin_adapters.
            if (canonical_name.startswith(NEVER_SANDBOX_PREFIXES)
                    and env_name not in SANDBOX_ELIGIBLE_CREDENTIALS):
                raise ValueError("orchestration credentials cannot be sandbox-entitled")
            if env_name not in self.ctx.env_passthrough:
                raise ValueError("trusted adapter credential is unavailable")
            if canonical_name.endswith("_BASE_URL"):
                validate_external_url(self.ctx.env_passthrough[env_name])
            allowed.add(env_name)
        return frozenset(allowed)

    def register_trusted_candidate(
        self,
        candidate: Candidate,
        credential_names,
    ) -> str:
        """Create the server-side capability needed to run an adapter with credentials.

        The one-use token must stay out of user/LLM-authored input; the server
        injects it under ``trusted_adapter_token`` in a private execution copy
        of the matching candidate spec only after authorization. It is consumed
        when the run is prepared. A candidate name alone never selects this
        registry or grants credentials. Credential names are exact (no
        prefix/wildcard matching), and system orchestration credentials are
        rejected permanently.
        """
        if not isinstance(candidate, Candidate) or not candidate.name:
            raise ValueError("trusted adapter must be a named Candidate")
        capability = secrets.token_urlsafe(32)
        self._trusted_adapter_registry[capability] = (
            copy.deepcopy(candidate),
            self._validated_adapter_credentials(credential_names),
        )
        return capability

    def _bind_adapter_credentials(self, candidate: Candidate, names) -> None:
        credentials = self._validated_adapter_credentials(names)
        # Validate before mutating: a rejected credential set must not clear an
        # existing binding, and must not leave a half-written one behind.
        self._revoke_adapter_credentials(candidate)
        self._adapter_entitlements[id(candidate)] = (candidate, credentials)

    def _revoke_adapter_credentials(self, candidate) -> None:
        """Drop the binding for this exact object, if it still owns the slot."""
        entry = self._adapter_entitlements.get(id(candidate))
        if entry is not None and entry[0] is candidate:
            del self._adapter_entitlements[id(candidate)]

    def _entitlements_for(self, candidate: Candidate) -> frozenset[str]:
        """Credentials granted to this exact object — never to a recycled id."""
        entry = self._adapter_entitlements.get(id(candidate))
        if entry is None or entry[0] is not candidate:
            return frozenset()
        return entry[1]

    def _prepare_run(self, spec: dict) -> None:
        """Reset per-attempt state and validate any host dataset path."""
        # Preserve the original offline-test injection convention only before
        # the first attempt. New callers should use register_candidate().
        if not self._attempt_started:
            for candidate in self.ctx.candidates.values():
                self.register_candidate(candidate)
        self._attempt_started = True
        cleanup_run_context(self.ctx)
        self._handle_to_candidate.clear()
        self.ctx.candidates.clear()
        self.ctx.citations.clear()
        self.ctx.result_keys.clear()
        self.ctx.results_initialized = True
        self.ctx.evaluated_metrics = None
        self.ctx.allowed_candidate_names.clear()
        self.ctx.allowed_doc_ids.clear()
        self._adapter_entitlements.clear()
        self._trusted_candidate_names.clear()
        self.ctx.ground_truth_path = ""
        self._active_spec = None
        self._dataset_path = ""
        for name in (
            "results.jsonl",
            "metrics.json",
            "pricing.json",
            "report.md",
            "report.pdf",
        ):
            path = os.path.join(self.run_dir, name)
            try:
                os.remove(path)
            except FileNotFoundError:
                pass

        candidates = spec.get("candidates") or []
        candidate_names = [str(candidate.get("name") or "") for candidate in candidates]
        if any(not name for name in candidate_names) or len(candidate_names) != len(
            set(candidate_names)
        ):
            raise ValueError("candidate names must be non-empty and unique")
        self.ctx.allowed_candidate_names.update(candidate_names)
        for candidate_spec, name in zip(candidates, candidate_names):
            capability = str(candidate_spec.get(TRUSTED_ADAPTER_TOKEN_FIELD) or "")
            if capability:
                trusted = self._trusted_adapter_registry.get(capability)
                if trusted is None or trusted[0].name != name:
                    raise ValueError("invalid trusted adapter capability")
                del self._trusted_adapter_registry[capability]
                attempt_candidate = copy.deepcopy(trusted[0])
                replace_candidate(self.ctx, name, attempt_candidate)
                self._bind_adapter_credentials(attempt_candidate, trusted[1])
                self._trusted_candidate_names.add(name)
                continue
            registered = self._registered_candidates.get(name)
            if registered is not None:
                replace_candidate(self.ctx, name, copy.deepcopy(registered))

        dataset = (spec.get("dataset") or {}).get("path")
        if dataset:
            upload_root, sample_dataset = _dataset_roots(self.runtime_env)
            # strict=True resolves symlinks before either check, so a link that
            # points outside is judged on its real target, not on where it sits.
            dataset_path = Path(dataset).resolve(strict=True)
            confined = dataset_path == upload_root or upload_root in dataset_path.parents
            if not confined and dataset_path != sample_dataset:
                raise ValueError(
                    "dataset path must be within the configured dataset root"
                )
            if not dataset_path.is_dir():
                raise ValueError("dataset path must be a directory")
            self.ctx.allowed_dataset_root = str(dataset_path)
            ground_truth = (dataset_path / "ground_truth.csv").resolve(strict=True)
            try:
                ground_truth.relative_to(dataset_path)
            except ValueError as exc:
                raise ValueError("ground truth is outside the dataset root") from exc
            self.ctx.ground_truth_path = str(ground_truth)
            # Only images that resolve to real files inside the dataset's own
            # images directory become addressable doc ids.
            self.ctx.allowed_doc_ids.update(
                Path(name).stem for name, _path in _resolved_images(dataset_path)
            )
            spec["dataset"]["path"] = str(dataset_path)
        else:
            self.ctx.allowed_dataset_root = ""

    def _run_with_cleanup(self, implementation, spec: dict) -> dict:
        if not self._run_lock.acquire(blocking=False):
            raise RuntimeError("a benchmark is already active for this orchestrator")
        try:
            self._prepare_run(spec)
            return implementation(spec)
        except Exception as exc:
            message = f"{type(exc).__name__}: benchmark execution failed"
            self.emit("error", {"message": message})
            raise
        finally:
            try:
                cleanup_run_context(self.ctx)
            finally:
                self._handle_to_candidate.clear()
                self._run_lock.release()

    # ------------------------------------------------------------------ events
    def _delta(self, text: str) -> None:
        self.emit("delta", {"text": self._redact(text)})

    def _state(self, phase: str, candidates: dict | None = None) -> None:
        self.emit(
            "state",
            {
                "phase": phase,
                "candidates": self._redact_data(candidates or {}),
            },
        )

    def _track_phase(self, name: str, args: dict) -> None:
        """Report the protocol phase implied by the tool the agent just called.

        Progress reporting only — it never decides anything. A candidate is
        named only when the tool argument matches a candidate this run already
        admitted, so a label invented by the model cannot reach the event
        stream, and an unrecognised tool reports no phase at all.
        """
        phase = TOOL_PHASES.get(name)
        if phase is None:
            return
        handle_id = str(args.get("id") or "")
        candidate = self._handle_to_candidate.get(handle_id) or str(
            args.get("label") or args.get("name") or ""
        )
        if candidate and candidate in self.ctx.allowed_candidate_names:
            self._state(phase, {candidate: phase.casefold()})
        else:
            self._state(phase)

    def _check_cancelled(self) -> None:
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise _RunCancelled("run stopped by user")

    # ------------------------------------------------------------------ chat
    def _extraction_request_is_complete(self, message: str) -> bool:
        """True when one request already carries what an extraction spec needs.

        Three things must hold: the session has a labelled dataset bound, the
        user states an extraction objective, and they name at least two
        candidates ProofBench itself recognises. When any of those is missing
        the normal clarifying reply stands — this only decides whether a
        non-spec answer is worth one internal retry, never what the spec says.
        """
        from engine.builtin_adapters import BUILTIN_ADAPTER_NAMES

        if not self.dataset_available:
            return False
        if not _EXTRACTION_INTENT.search(message or ""):
            return False
        compact = _compact(message)
        named = {name for name in BUILTIN_ADAPTER_NAMES if _compact(name) in compact}
        return len(named) >= 2

    def chat(self, user_message: str) -> None:
        """INTAKE/DISCOVERY conversation; emits deltas and eventually a spec artifact."""
        system_prompt = intake_system(self.dataset_available)
        if not self._messages:
            self._messages = [{"role": "system", "content": system_prompt}]
        else:
            self._messages[0] = {"role": "system", "content": system_prompt}
        self._messages.append({"role": "user", "content": user_message})
        client = _orchestrator_client(self.runtime_env)
        schemas = [s for s in TOOL_SCHEMAS if s["function"]["name"] in CHAT_TOOLS]
        # At most one internal retry, and only when the request already answers
        # everything the spec needs. Genuine clarification is still returned.
        retry_available = self._extraction_request_is_complete(user_message)

        for _ in range(8):  # bounded intake loop
            self._check_cancelled()
            kwargs = {"model": _orchestrator_model(self.runtime_env), "messages": self._messages}
            if schemas:
                kwargs["tools"] = schemas
            resp = client.chat.completions.create(**kwargs)
            msg = resp.choices[0].message

            if msg.tool_calls:
                self._messages.append(msg.model_dump(exclude_none=True))
                for tc in msg.tool_calls:
                    args = json.loads(tc.function.arguments or "{}")
                    result = dispatch_tool(tc.function.name, args, self.ctx)
                    self._messages.append(
                        {"role": "tool", "tool_call_id": tc.id,
                         "content": self._tool_result_for_history(tc.function.name, args, result)}
                    )
                continue

            text = msg.content or ""
            spec = self._extract_spec(text)
            if spec is None and retry_available:
                # Ask once more inside the same operation so a complete request
                # does not cost the user a second confirmation turn. The
                # unemitted reply stays in the private message list only.
                retry_available = False
                self._messages.append({"role": "assistant", "content": text})
                self._messages.append({"role": "user", "content": SPEC_RETRY_NUDGE})
                continue
            self._messages.append({"role": "assistant", "content": text})
            self._delta(text)
            if spec:
                self.emit("artifact", {"kind": "spec", "spec": spec})
                self._state("SPEC_CONFIRM")
            return

        self._delta("I'm going in circles — please rephrase what you'd like to benchmark.")

    def _extract_spec(self, text: str) -> dict | None:
        m = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
        if not m:
            return None
        try:
            spec = json.loads(_strip_json_comments_and_trailing_commas(m.group(1)))
        except json.JSONDecodeError:
            return None
        return _normalize_intake_spec(spec, self.dataset_available)

    def _report_unavailable(self) -> None:
        """Expose an optional-artifact failure without discarding measurements."""
        warning = {
            "artifact": "report",
            "message": "Report rendering failed; measured metrics are available.",
        }
        self.artifact_warnings.append(warning)
        self.emit("artifact", {"kind": "report", "available": False,
                               "warning": warning["message"], "provenance": "measured"})

    # ------------------------------------------------------ benchmark dispatch
    def run_benchmark(self, spec: dict) -> dict:
        """Run a benchmark without granting a model authority over result records."""
        if spec.get("benchmark_type") == "tool_assessment":
            return self.run_tool_assessment(spec)
        # Adapter discovery may use a provider, but the engine alone invokes
        # each adapter over every admitted image and writes evaluator input.
        # An LLM must never author a dataset runner or append a result record.
        return self.run_benchmark_scripted(spec)

    def _run_benchmark_impl(self, spec: dict) -> dict:
        """Compatibility seam; extraction no longer has autonomous tool driving."""
        if spec.get("benchmark_type") != "tool_assessment":
            return self._run_benchmark_scripted_impl(spec)
        self._active_spec = spec
        if spec.get("benchmark_type") == "tool_assessment":
            return self._run_tool_assessment_impl(spec)
        self._state("DOCS_INTEL", {c["name"]: "pending" for c in spec["candidates"]})
        dataset = spec["dataset"]["path"]
        ground_truth = os.path.join(dataset, "ground_truth.csv")
        images = self._list_images(dataset)
        if not images:
            raise RuntimeError("dataset contains no usable images")

        # Prepare everything the LLM needs: env, handles reserved later.
        brief = (
            f"Benchmark spec:\n{json.dumps(spec, indent=2)}\n\n"
            f"Dataset ground truth: {ground_truth}\n"
            f"Results file: {self.results_path}\n"
            f"Number of documents: {len(images)} (already uploaded per sandbox by the engine; "
            f"reference them as images/<name>.png).\n"
            "Begin with DOCS_INTEL."
        )
        messages = [
            {"role": "system", "content": RUN_SYSTEM},
            {"role": "user", "content": brief},
        ]

        client = _orchestrator_client(self.runtime_env)
        calls = 0
        consecutive_errors = 0
        finished = False

        while calls < MAX_TOOL_CALLS and not finished:
            self._check_cancelled()
            resp = client.chat.completions.create(
                model=_orchestrator_model(self.runtime_env), messages=messages, tools=TOOL_SCHEMAS
            )
            msg = resp.choices[0].message
            if not msg.tool_calls:
                text = (msg.content or "").strip()
                if text:
                    self._delta(text)
                if "DONE" in text:
                    finished = True
                    break
                messages.append({"role": "assistant", "content": text})
                messages.append(
                    {"role": "user", "content": "Continue to the next phase of the protocol."}
                )
                continue

            messages.append(msg.model_dump(exclude_none=True))
            for tc in msg.tool_calls:
                self._check_cancelled()
                calls += 1
                name = tc.function.name
                args = json.loads(tc.function.arguments or "{}")
                try:
                    result = self._dispatch_with_collation(name, args)
                    consecutive_errors = 0
                except Exception as e:  # keep the loop alive; let the agent see the error
                    result = json.dumps(
                        {"error": f"{type(e).__name__}: tool execution failed"}
                    )
                    consecutive_errors += 1
                self._track_phase(name, args)
                messages.append(
                    {"role": "tool", "tool_call_id": tc.id,
                     "content": self._tool_result_for_history(name, args, result)}
                )
            if consecutive_errors >= 3:
                self._delta("Agent loop hit repeated errors — switching to scripted pipeline.")
                break

        # Fallback: any candidate with no results gets the deterministic pipeline.
        done_candidates = self._candidates_with_results()
        missing = [c for c in spec["candidates"] if c["name"] not in done_candidates]
        if missing:
            self._delta(f"Completing {len(missing)} candidate(s) via scripted pipeline.")
            self._run_candidates_scripted(missing, images)

        return self._evaluate_and_report(ground_truth)

    def run_tool_assessment(self, spec: dict) -> dict:
        """Run a documentation assessment with lifecycle isolation."""
        return self._run_with_cleanup(self._run_tool_assessment_impl, spec)

    def _run_tool_assessment_impl(self, spec: dict) -> dict:
        """Assess arbitrary tools from docs and use Daytona only for viable integrations."""
        from engine.tool_assessment import (
            ASSESSMENT_VERIFICATION_ENTITLEMENTS,
            assess_documentation_batch,
            assessment_provider,
            result_from_plan,
            unavailable_result,
            write_assessment_report,
        )

        candidates = spec.get("candidates") or []
        objective = str(spec.get("objective") or spec.get("category") or "implementation assessment")
        metrics: dict[str, dict] = {}
        statuses = {str(candidate.get("name") or "candidate"): "pending" for candidate in candidates}
        self._state("DOCS_INTEL", statuses)
        scraped_candidates: list[dict[str, str]] = []
        candidate_by_name = {
            str(candidate.get("name") or "candidate"): candidate for candidate in candidates
        }

        for candidate_spec in candidates:
            self._check_cancelled()
            name = str(candidate_spec.get("name") or "candidate")
            display_name = str(candidate_spec.get("display_name") or name)
            docs_url = str(candidate_spec.get("docs_url") or "").strip()
            if not docs_url:
                metrics[name] = unavailable_result("No official implementation documentation URL was provided.")
                statuses[name] = "skipped"
                self._state("DOCS_INTEL", dict(statuses))
                continue

            try:
                scraped = dispatch_tool("scrape_docs", {"url": docs_url}, self.ctx)
                docs_value = json.loads(scraped)
                if isinstance(docs_value, dict) and docs_value.get("error"):
                    raise RuntimeError(str(docs_value["error"]))
                docs_text = str(docs_value)
                safe_docs_url = self._redact(docs_url)
                for citation in self.ctx.citations:
                    if citation.get("url") == safe_docs_url:
                        citation["title"] = self._redact(
                            f"{display_name} documentation"
                        )
                scraped_candidates.append({"name": name, "docs_text": docs_text})
                statuses[name] = "queued"
                self._state("DOCS_INTEL", dict(statuses))
            except _RunCancelled:
                raise
            except Exception as exc:
                metrics[name] = unavailable_result(
                    f"Documentation scrape failed: {type(exc).__name__}"
                )
                statuses[name] = "skipped"
                self.emit("artifact", {
                    "kind": "trace",
                    "tool": "scrape_docs",
                    "args_summary": name,
                    "status": "error",
                    "detail": f"{type(exc).__name__}: documentation scrape failed",
                })
                self._state("DOCS_INTEL", dict(statuses))
                continue

        assessments: dict[str, dict] = {}
        if scraped_candidates:
            self._state("ADAPTER_GEN", {
                **statuses,
                **{candidate["name"]: "batching" for candidate in scraped_candidates},
            })
            try:
                provider = assessment_provider(self.runtime_env)
            except Exception:
                provider = "unconfigured"
            summary = f"{len(scraped_candidates)} implementation assessments via {provider}"
            self.emit("artifact", {
                "kind": "trace",
                "tool": "assess_documentation_batch",
                "args_summary": summary,
                "status": "start",
            })
            self._delta(
                f"Submitting {len(scraped_candidates)} documentation assessments to {provider} as one batch.\n"
            )
            try:
                assessments = assess_documentation_batch(
                    scraped_candidates,
                    objective,
                    env=self.runtime_env,
                    entitled_credentials=ASSESSMENT_VERIFICATION_ENTITLEMENTS,
                )
                failures = sum(1 for result in assessments.values() if result.get("error"))
                self.emit("artifact", {
                    "kind": "trace",
                    "tool": "assess_documentation_batch",
                    "args_summary": summary,
                    "status": "ok" if failures < len(scraped_candidates) else "error",
                    "detail": f"{len(scraped_candidates) - failures} completed, {failures} failed",
                })
            except Exception as exc:
                assessments = {
                    candidate["name"]: {
                        "error": f"{type(exc).__name__}: assessment failed"
                    }
                    for candidate in scraped_candidates
                }
                self.emit("artifact", {
                    "kind": "trace",
                    "tool": "assess_documentation_batch",
                    "args_summary": summary,
                    "status": "error",
                    "detail": f"{type(exc).__name__}: assessment failed",
                })

        for scraped_candidate in scraped_candidates:
            self._check_cancelled()
            name = scraped_candidate["name"]
            candidate_spec = candidate_by_name[name]
            display_name = str(candidate_spec.get("display_name") or name)
            assessment = assessments.get(name) or {"error": "the provider returned no assessment"}
            if assessment.get("error"):
                # A provider failure is not evidence about this tool, so the row
                # is marked unavailable and its scores are withheld rather than
                # persisted as a zero that would read as a genuine bad result.
                metrics[name] = unavailable_result(
                    self._redact(f"Assessment unavailable: {assessment['error']}")
                )
                statuses[name] = "skipped"
                self._state("EVALUATING", dict(statuses))
                continue

            plan = assessment["plan"]
            self.emit("artifact", {
                "kind": "trace",
                "tool": "assess_implementation",
                "args_summary": f"{name} documentation feasibility",
                "status": "ok",
                "detail": self._redact(plan["reason"])[:200],
            })
            if not plan["implementable"]:
                metrics[name] = result_from_plan(plan, "not_implementable", False)
                statuses[name] = "rated"
                self._state("EVALUATING", dict(statuses))
                self._delta(f"{display_name}: documentation was insufficient for a credible implementation. Daytona skipped.\n")
                continue
            if plan["execution_mode"] == "comparison_only":
                # Cloud, SaaS, credentialed, destructive, or otherwise unrunnable
                # products are compared from documentation evidence. No sandbox
                # is provisioned and no execution is implied.
                metrics[name] = result_from_plan(plan, "not_applicable", False)
                statuses[name] = "rated"
                self._state("EVALUATING", dict(statuses))
                self._delta(
                    f"{display_name}: compared from documentation evidence. "
                    "Not executable without credentials, so Daytona was not used.\n"
                )
                continue

            handle = None
            verification_status = "failed"
            try:
                statuses[name] = "provisioning"
                self._state("PROVISIONING", dict(statuses))
                handle = self.pool.acquire(name)
                self._log(name, "Daytona sandbox allocated from documented implementation plan", "building")
                statuses[name] = "building"
                self._state("BUILDING", dict(statuses))
                for command in plan["build_commands"]:
                    self._check_cancelled()
                    self._log(name, f"$ {command}", "building")
                    output = self.pool.exec(handle, command, timeout=300)
                    for line in output.splitlines()[-5:]:
                        self._log(name, line[:300], "building")

                statuses[name] = "validating"
                self._state("VALIDATING", dict(statuses))
                code = plan["verification_code"]
                # Same set advertised to the planner above, by construction.
                code = env_prelude(
                    code,
                    self.ctx.env_passthrough,
                    ASSESSMENT_VERIFICATION_ENTITLEMENTS,
                )
                output = self.pool.run_python(handle, code, timeout=180)
                verification_status = "passed" if "PROOFBENCH_OK" in output else "failed"
                self._log(name, f"implementation verification: {verification_status}", "validating")
            except _RunCancelled:
                raise
            except Exception as exc:
                self._log(
                    name,
                    f"implementation verification failed: {type(exc).__name__}",
                    "failed",
                )
                verification_status = "failed"
            finally:
                if handle is not None:
                    self.pool.release(handle)

            metrics[name] = result_from_plan(plan, verification_status, True)
            statuses[name] = "done" if verification_status == "passed" else "failed"
            self._state("EVALUATING", dict(statuses))

        self._state("EVALUATING", dict(statuses))
        metrics = self._redact_data(metrics)
        safe_citations = self._redact_data(self.ctx.citations)
        metrics_path = os.path.join(self.run_dir, "metrics.json")
        with open(metrics_path, "w", encoding="utf-8") as handle:
            json.dump({"provenance": "measured", "metrics": metrics}, handle, indent=2)
        self.emit(
            "artifact",
            {"kind": "results", "metrics": metrics, "provenance": "measured"},
        )

        self._state("REPORTING", dict(statuses))
        try:
            report = write_assessment_report(metrics, safe_citations, os.devnull)
            report = self._redact(report)
            with open(os.path.join(self.run_dir, "report.md"), "w", encoding="utf-8") as handle:
                handle.write(report)
            from engine.pdf_report import write_pdf_report

            write_pdf_report(metrics, report, os.path.join(self.run_dir, "report.pdf"))
        except _RunCancelled:
            # A stop request stays a stop request; it is not an artifact warning.
            raise
        except Exception:
            # Metrics have already been written and emitted.  Reports are an
            # optional rendering step, not a reason to erase real evidence.
            self._report_unavailable()
        else:
            self.emit("artifact", {
                "kind": "report",
                "markdown": report,
                "citations": safe_citations,
                "provenance": "measured",
            })
        self._state("DONE", dict(statuses))
        return metrics

    # --------------------------------------------------------- scripted run mode
    def run_benchmark_scripted(self, spec: dict) -> dict:
        """Run the deterministic pipeline with lifecycle isolation."""
        return self._run_with_cleanup(self._run_benchmark_scripted_impl, spec)

    def _run_benchmark_scripted_impl(self, spec: dict) -> dict:
        """Deterministic pipeline, using the same building blocks without an LLM."""
        self._active_spec = spec
        dataset = spec["dataset"]["path"]
        ground_truth = os.path.join(dataset, "ground_truth.csv")
        images = self._list_images(dataset)
        if not images:
            raise RuntimeError("dataset contains no usable images")
        self._state("PROVISIONING", {c["name"]: "pending" for c in spec["candidates"]})
        self._prepare_generated_adapters(spec["candidates"])
        self.pool.size = min(4, max(1, len(spec["candidates"])))
        self.pool.start()
        self._run_candidates_scripted(spec["candidates"], images)
        return self._evaluate_and_report(ground_truth)

    def _run_candidates_scripted(self, candidates: list[dict], images: list[str]) -> None:
        with ThreadPoolExecutor(max_workers=4) as ex:
            list(ex.map(self._candidate_pipeline, candidates))

    def _prepare_generated_adapters(self, candidates: list[dict]) -> None:
        """Fetch docs and generate each non-built-in adapter once, before execution.

        This is the only LLM-assisted part of an extraction run.  The returned
        Candidate is then invoked by the engine's fixed adapter wrapper for
        every authorized image; model output never becomes a runner or result.
        """
        from engine.adapter_gen import generate_adapter
        from engine.builtin_adapters import is_builtin_adapter
        from engine.docs_intel import scrape_page

        for candidate_spec in candidates:
            name = str(candidate_spec.get("name") or "")
            uses_builtin = candidate_spec.get("use_fallback", True) and is_builtin_adapter(name)
            if uses_builtin:
                continue
            docs_url = str(candidate_spec.get("docs_url") or "")
            self._state("DOCS_INTEL", {name: "fetching"})
            try:
                if not docs_url:
                    raise ValueError("documentation URL is required for generated adapters")
                docs = scrape_page(docs_url, env=self.runtime_env)
                self.ctx.citations.append({"title": f"{name} documentation", "url": docs_url})
                self._state("ADAPTER_GEN", {name: "generating"})
                generated = generate_adapter(name, docs, env=self.runtime_env)
                generated.name = name
                generated.docs_url = docs_url
                generated.pricing_url = str(candidate_spec.get("pricing_url") or "")
                generated.kind = str(candidate_spec.get("kind") or generated.kind)
                self.ctx.candidates[name] = generated
                self.emit("artifact", {"kind": "trace", "tool": "generate_adapter",
                                       "args_summary": name, "status": "ok"})
            except Exception as exc:
                self.emit("artifact", {"kind": "trace", "tool": "generate_adapter",
                                       "args_summary": name, "status": "error",
                                       "detail": f"{type(exc).__name__}: adapter preparation failed"})

    # ------------------------------------------------------------- pipeline blocks
    def _candidate_pipeline(self, cand_spec: dict) -> None:
        """Build → validate (repair once → fallback) → run dataset, for one candidate."""
        name = cand_spec["name"]
        try:
            candidate = self._resolve_candidate(cand_spec)
            if candidate is None:
                self._fail_candidate(name, "no adapter available for this candidate")
                return
            handle = self.pool.acquire(name)
            self._handle_to_candidate[handle.id] = name
            try:
                self._upload_dataset(handle)
                self._state("BUILDING", {name: "building"})
                self._build(handle, candidate)
                self._state("VALIDATING", {name: "validating"})
                if not self._validate(handle, candidate):
                    # A trusted built-in already IS the first-party adapter, and
                    # a re-loaded copy would carry no credential entitlement.
                    # Retrying it would only manufacture a second failure.
                    fb = (None if name in self._trusted_candidate_names
                          else self._try_fallback(cand_spec))
                    if fb is None:
                        self._fail_candidate(name, "adapter validation failed")
                        return
                    candidate = fb
                    self._build(handle, candidate)
                    if not self._validate(handle, candidate):
                        self._fail_candidate(name, "fallback adapter validation failed")
                        return
                self._state("RUNNING", {name: "running"})
                self._run_dataset(handle, candidate)
                self._state("RUNNING", {name: "done"})
            finally:
                self.pool.release(handle)
        except _RunCancelled:
            raise
        except Exception as e:
            self.emit(
                "artifact",
                {"kind": "trace", "tool": "pipeline", "args_summary": name,
                 "status": "error",
                 "detail": f"{type(e).__name__}: candidate pipeline failed"},
            )
            self._fail_candidate(name, f"{type(e).__name__}: candidate pipeline failed")

    def _fail_candidate(self, name: str, reason: str) -> None:
        """Record a candidate's failure on every document instead of omitting it.

        Without this, a candidate that never produced a result record simply
        disappears from the metrics and the run reads as if it had not been
        requested. The records are genuine failures, so the evaluator reports
        the candidate with real zero accuracy and a full failure count.
        """
        self._state("RUNNING", {name: "failed"})
        detail = self._redact(reason)[:300]
        doc_ids = [os.path.splitext(image)[0]
                   for image in self._list_images(self._dataset_path)]
        for doc_id in doc_ids:
            append_result_record(self.ctx, {
                "candidate": name,
                "doc_id": doc_id,
                "ok": False,
                "prediction": None,
                "latency_s": 0.0,
                "error": detail,
            })

    def _upload_dataset(self, handle) -> None:
        """Upload images + ground truth into the sandbox (real sandboxes start empty)."""
        dataset = self._dataset_path
        if not dataset:
            return
        dataset_path = Path(dataset).resolve(strict=True)
        allowed_root = self.ctx.allowed_dataset_root
        # Re-check at the point of use: the prepared root is the only dataset
        # this run may read from, whatever the spec says now.
        if allowed_root and dataset_path != Path(allowed_root):
            raise ValueError("dataset path is not the prepared dataset root")
        images = _resolved_images(dataset_path)
        for name, path in images:
            self.pool.upload(handle, str(path), f"images/{name}")
        gt = dataset_path / "ground_truth.csv"
        if gt.exists():
            resolved_gt = gt.resolve(strict=True)
            if resolved_gt.parent != dataset_path or not resolved_gt.is_file():
                raise ValueError("ground truth is outside the dataset root")
            self.pool.upload(handle, str(resolved_gt), "ground_truth.csv")
        self._log(handle.label, f"uploaded {len(images)} images + ground truth", "building")

    def _resolve_candidate(self, cand_spec: dict) -> Candidate | None:
        """Generated adapter (if available) else fallback registry."""
        name = cand_spec["name"]
        if name in self.ctx.candidates:  # LLM-generated earlier in run_benchmark
            return self.ctx.candidates[name]
        if cand_spec.get("use_fallback", True):
            return self._try_fallback(cand_spec)
        # Unknown discovered tool with no generated adapter: cannot run.
        self.emit("artifact", {"kind": "trace", "tool": "resolve", "args_summary": name,
                               "status": "error", "detail": "no adapter available"})
        return None

    def _try_fallback(self, cand_spec: dict) -> Candidate | None:
        from engine.adapter_gen import get_fallback

        fb = get_fallback(cand_spec["name"])
        if fb:
            fb.docs_url = cand_spec.get("docs_url", fb.docs_url)
            fb.pricing_url = cand_spec.get("pricing_url", fb.pricing_url)
        return fb

    def _build(self, handle, candidate: Candidate) -> None:
        for cmd in candidate.build_commands:
            self._check_cancelled()
            self._log(handle.label, f"$ {cmd}", "building")
            out = self.pool.exec(handle, cmd, timeout=300)
            for line in out.splitlines()[-5:]:
                self._log(handle.label, line[:300], "building")

    def _validate(self, handle, candidate: Candidate) -> bool:
        code = self._adapter_code(candidate, "images/" + self._first_image())
        out = self.pool.run_python(handle, code, timeout=180)
        ok = self._collate_probe(out)
        self._log(handle.label, "validation: " + ("ok" if ok else "FAILED"), "validating")
        if not ok:
            # Keep a bounded, secret-redacted diagnostic in the trace so a real
            # integration failure is actionable instead of collapsing to the
            # unhelpful phrase "adapter validation failed".
            diagnostic_lines = [line.strip() for line in self._redact(out).splitlines()
                                if line.strip()][-6:]
            for line in diagnostic_lines:
                self._log(handle.label, f"validation error: {line[:260]}", "validating")
        if not ok and candidate.name not in self._trusted_candidate_names:
            repaired = self._repair_once(candidate, self._redact(out))
            if repaired is not None:
                candidate.adapter_code = repaired
                candidate.setup_complexity = min(5, candidate.setup_complexity + 1)
                out = self.pool.run_python(
                    handle, self._adapter_code(candidate, "images/" + self._first_image()),
                    timeout=180,
                )
                ok = self._collate_probe(out)
                self._log(handle.label,
                          "repair attempt: " + ("ok" if ok else "FAILED"), "validating")
        return ok

    def _repair_once(self, candidate: Candidate, error_output: str) -> str | None:
        """Ask the codegen worker to fix the adapter. Returns new code or None."""
        from engine.llm_clients import capability_providers

        if not capability_providers("codegen", self.runtime_env):
            return None
        try:
            from engine.adapter_gen import repair_adapter

            repaired = repair_adapter(
                candidate.adapter_code, error_output[-2000:], env=self.runtime_env
            )
            # Repaired code is model-authored and no longer the reviewed adapter
            # the credentials were granted to.
            self._revoke_adapter_credentials(candidate)
            return repaired
        except AttributeError:
            return None  # adapter_gen has no repair_adapter; scripted fallback takes over
        except Exception:
            return None

    def _run_dataset(self, handle, candidate: Candidate) -> None:
        images = self._list_images(self._dataset_path)
        if not candidate.batch_safe:
            for image in images:
                self._check_cancelled()
                relative = f"images/{image}"
                out = self.pool.run_python(
                    handle, self._adapter_code(candidate, relative), timeout=180,
                )
                self._collate(out, candidate.name, doc_id=os.path.splitext(image)[0])
                self._log(handle.label, f"ran {relative}", "running")
            return
        self._check_cancelled()
        out = self.pool.run_python(
            handle, self._adapter_batch_code(candidate, images),
            timeout=max(180, 180 * len(images)),
        )
        self._collate(out, candidate.name)
        produced = {str(item.get("doc_id") or item.get("image") or "")
                    for item in self._extract_result_lines(self._redact(out))}
        for image in images:
            doc_id = os.path.splitext(image)[0]
            if doc_id not in produced:
                append_result_record(self.ctx, {
                    "candidate": candidate.name,
                    "doc_id": doc_id,
                    "ok": False,
                    "prediction": None,
                    "latency_s": 0.0,
                    "error": "adapter emitted no result for this document",
                })
            self._log(handle.label, f"ran images/{image}", "running")

    # --------------------------------------------------------------- collation
    def _dispatch_with_collation(self, name: str, args: dict) -> str:
        result = dispatch_tool(name, args, self.ctx)
        if name == "spawn_sandbox":
            try:
                info = json.loads(result)
                hid = info.get("id", "")
                self._handle_to_candidate[hid] = args.get("label", "")
                # the agent is told the dataset is "already uploaded" — make it true
                handle = self.ctx.sandbox_handles.get(hid)
                if handle is not None:
                    self._upload_dataset(handle)
            except Exception:
                pass
        if name in ("run_python_in_sandbox", "exec_in_sandbox"):
            candidate = self._handle_to_candidate.get(args.get("id", ""), args.get("id", ""))
            self._collate(result, candidate)
        return result

    @staticmethod
    def _extract_result_lines(output: str) -> list[dict]:
        found = []
        if len(str(output).encode("utf-8")) > 4 * 1024 * 1024:
            raise ValueError("sandbox output exceeds the collation limit")
        try:
            payload = json.loads(output)
            if isinstance(payload, dict) and "output" in payload:
                output = str(payload["output"])
        except Exception:
            pass
        for line in str(output).splitlines():
            line = line.strip()
            if line.startswith("RESULT_JSON:"):
                try:
                    payload = line[len("RESULT_JSON:"):]
                    if len(payload.encode("utf-8")) > MAX_RESULT_RECORD_BYTES:
                        raise ValueError("result record exceeds the allowed size")
                    result = json.loads(payload)
                    if not isinstance(result, dict):
                        raise ValueError("invalid result payload")
                    found.append(result)
                except json.JSONDecodeError as exc:
                    raise ValueError("invalid result payload") from exc
        return found

    def _collate(self, output: str, candidate: str, doc_id: str | None = None) -> None:
        for r in self._extract_result_lines(self._redact(output)):
            if not isinstance(r.get("ok"), bool):
                raise ValueError("invalid result status")
            doc = r.get("doc_id") or r.get("image") or doc_id or "unknown"
            record = {
                "candidate": candidate,
                "doc_id": doc,
                "ok": r["ok"],
                "prediction": r.get("fields") if r["ok"] else None,
                "latency_s": r.get("latency_s", 0.0),
                "error": None if r["ok"] else r.get("error", "unknown error"),
            }
            append_result_record(self.ctx, record)

    def _collate_probe(self, output: str) -> bool:
        return any(r.get("ok") for r in self._extract_result_lines(output))

    def _candidates_with_results(self) -> set[str]:
        done = set()
        if os.path.exists(self.results_path):
            with open(self.results_path, encoding="utf-8") as f:
                for line in f:
                    try:
                        done.add(json.loads(line)["candidate"])
                    except Exception:
                        continue
        return done

    # ------------------------------------------------------------- evaluate/report
    def _evaluate_and_report(self, ground_truth: str) -> dict:
        self._state("EVALUATING")
        from engine.evaluate import evaluate_results

        if not self.ctx.ground_truth_path:
            raise RuntimeError("evaluation dataset is not configured for this run")
        if os.path.abspath(ground_truth) != os.path.abspath(self.ctx.ground_truth_path):
            raise ValueError("ground truth does not match the current run capability")
        ground_truth = self.ctx.ground_truth_path

        pricing = {}
        pricing_path = os.path.join(self.run_dir, "pricing.json")
        if os.path.exists(pricing_path):
            with open(pricing_path, encoding="utf-8") as f:
                pricing = json.load(f)
        metrics = {}
        if os.path.exists(self.results_path):
            metrics = evaluate_results(self.results_path, ground_truth, pricing=pricing)
        if not metrics:
            raise RuntimeError(
                "real benchmark produced no valid result records; no metrics were generated"
            )
        metrics = self._redact_data(metrics)
        safe_citations = self._redact_data(self.ctx.citations)
        with open(os.path.join(self.run_dir, "metrics.json"), "w", encoding="utf-8") as f:
            json.dump({"provenance": "measured", "metrics": metrics}, f, indent=2)
        self.emit(
            "artifact",
            {"kind": "results", "metrics": metrics, "provenance": "measured"},
        )

        self._state("REPORTING")
        from engine.report_gen import write_report

        try:
            report = write_report(metrics, safe_citations, os.devnull, env=self.runtime_env)
            report = self._redact(report)
            with open(os.path.join(self.run_dir, "report.md"), "w", encoding="utf-8") as handle:
                handle.write(report)
            from engine.pdf_report import write_pdf_report

            write_pdf_report(metrics, report, os.path.join(self.run_dir, "report.pdf"))
        except _RunCancelled:
            # A stop request stays a stop request; it is not an artifact warning.
            raise
        except Exception:
            self._report_unavailable()
        else:
            self.emit("artifact", {"kind": "report", "markdown": report,
                                   "citations": safe_citations,
                                   "provenance": "measured"})
        self._state("DONE")
        return metrics

    # ------------------------------------------------------------------ helpers
    _dataset_path: str = ""

    def _list_images(self, dataset: str) -> list[str]:
        self._dataset_path = dataset
        return [name for name, _path in _resolved_images(dataset)]

    def _first_image(self) -> str:
        images = self._list_images(self._dataset_path)
        return images[0] if images else "missing.png"

    def _adapter_code(self, candidate: Candidate, image_path: str) -> str:
        code = candidate.adapter_code
        if "RESULT_JSON:" not in code:
            code = code + "\n" + RESULT_JSON_WRAPPER
        argv_patch = f"import sys\nsys.argv = ['adapter', {image_path!r}]\n"
        return env_prelude(
            argv_patch + code,
            self.ctx.env_passthrough,
            self._entitlements_for(candidate),
        )

    def _adapter_batch_code(self, candidate: Candidate, images: list[str]) -> str:
        """Engine-authored batch runner; model code can only define ``extract``."""
        code = candidate.adapter_code.rstrip()
        if code.endswith(RESULT_JSON_WRAPPER):
            code = code[:-len(RESULT_JSON_WRAPPER)].rstrip()
        else:
            raise ValueError("candidate adapter does not end with the required result wrapper")
        documents = [(os.path.splitext(name)[0], f"images/{name}") for name in images]
        runner = f'''
import json as _pb_json, time as _pb_time
for _pb_doc_id, _pb_image in {documents!r}:
    _pb_started = _pb_time.time()
    try:
        _pb_fields = extract(_pb_image)
        _pb_result = {{"ok": True, "fields": _pb_fields,
                      "latency_s": round(_pb_time.time() - _pb_started, 3),
                      "doc_id": _pb_doc_id}}
    except Exception as _pb_exc:
        _pb_result = {{"ok": False,
                      "error": f"{{type(_pb_exc).__name__}}: {{_pb_exc}}",
                      "latency_s": round(_pb_time.time() - _pb_started, 3),
                      "doc_id": _pb_doc_id}}
    print("RESULT_JSON:" + _pb_json.dumps(_pb_result))
'''
        return env_prelude(
            code + "\n" + runner,
            self.ctx.env_passthrough,
            self._entitlements_for(candidate),
        )

    def _log(self, sandbox: str, line: str, phase: str) -> None:
        self.emit("artifact", {"kind": "sandbox_log", "sandbox": sandbox,
                               "line": self._redact(line)[:300], "phase": phase})
