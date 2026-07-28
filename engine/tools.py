"""Agent tool layer for ProofBench (CONTRACTS §8).

Exposes TOOL_SCHEMAS (OpenAI function-calling format) and dispatch_tool().
Sibling engine modules (docs_intel, adapter_gen, evaluate, report_gen,
sandbox_pool) are imported lazily inside the tool functions so this module
always imports cleanly, even while other lanes' files are mid-write.

All return values are JSON strings suitable for tool-role chat messages.
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from engine.sandbox_pool import SandboxHandle, SandboxPool

# ---------------------------------------------------------------------------
# Run context
# ---------------------------------------------------------------------------


@dataclass
class RunContext:
    """Per-run state threaded through every tool call."""

    run_id: str
    run_dir: str
    pool: SandboxPool
    emit: Callable[[str, dict], None]
    results_path: str
    candidates: dict = field(default_factory=dict)
    citations: list = field(default_factory=list)
    env_passthrough: dict = field(default_factory=dict)  # sandbox adapter env, never logged
    runtime_env: dict = field(default_factory=dict, repr=False)
    allowed_dataset_root: str = ""
    ground_truth_path: str = ""
    allowed_candidate_names: set[str] = field(default_factory=set)
    allowed_doc_ids: set[str] = field(default_factory=set)
    evaluated_metrics: dict | None = field(default=None, repr=False)
    sandbox_handles: dict[str, SandboxHandle] = field(default_factory=dict, repr=False)
    result_keys: set[tuple[str, str]] = field(default_factory=set, repr=False)
    result_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    results_initialized: bool = field(default=False, repr=False)
    # Set by the orchestrator. Called with a Candidate that is about to lose its
    # place in `candidates` so any credentials bound to that exact object are
    # revoked before it becomes unreachable. See replace_candidate().
    revoke_entitlements: Callable[[Any], None] | None = field(default=None, repr=False)


def replace_candidate(ctx: RunContext, name: str, candidate: Any) -> None:
    """Install `candidate` under `name`, revoking whatever it displaces.

    Credential entitlements are keyed by object identity. A displaced Candidate
    must lose its binding *before* it becomes unreachable: otherwise CPython is
    free to recycle its id() for a later generated adapter, which would then
    inherit the displaced object's credentials. Every write to ctx.candidates
    after the run is prepared must go through here.
    """
    displaced = ctx.candidates.get(name)
    if displaced is not None and displaced is not candidate and ctx.revoke_entitlements:
        ctx.revoke_entitlements(displaced)
    ctx.candidates[name] = candidate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SECRET_KEY_RE = re.compile(r"(?i)(key|token|pass|secret)")
_ENV_REFERENCE_RE = re.compile(
    r"(?:\benviron\s*(?:\.get\s*\(|\[)|\bgetenv\s*\()\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]"
)
_MAX_SUMMARY = 120
_MAX_DETAIL = 200  # SSE log lines must be single-line, <=300 chars (§14)
_MAX_OUTPUT = 4000
MAX_RESULT_RECORD_BYTES = 32 * 1024
MAX_RESULT_FIELD_CHARS = 2048
MAX_RESULT_ERROR_CHARS = 4096
MAX_RESULT_ID_CHARS = 128


def _secret_values(ctx: RunContext) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                str(value)
                for key, value in ctx.env_passthrough.items()
                if value is not None and str(value) and _SECRET_KEY_RE.search(str(key))
            },
            key=len,
            reverse=True,
        )
    )


def redact_secret_values(value: Any, secrets: tuple[str, ...] | list[str]) -> str:
    """Redact configured secret values wherever they occur, regardless of key."""
    rendered = "" if value is None else str(value)
    for secret in secrets:
        if not secret:
            continue
        rendered = rendered.replace(secret, "***")
        escaped = json.dumps(secret, ensure_ascii=False)[1:-1]
        if escaped != secret:
            rendered = rendered.replace(escaped, "***")
    return rendered


def redact_data(value: Any, secrets: tuple[str, ...] | list[str]) -> Any:
    """Value-redact a JSON-compatible structure while preserving its shape."""
    serialized = json.dumps(value, ensure_ascii=False)
    return json.loads(redact_secret_values(serialized, secrets))


def _scrub_value(value: Any, secrets: tuple[str, ...], limit: int = 60) -> str:
    """One-line, length-capped rendering of an arg value for the trace feed."""
    s = value if isinstance(value, str) else repr(value)
    s = redact_secret_values(s, secrets)
    s = s.replace("\r", " ").replace("\n", " ")
    return s[:limit] + ("..." if len(s) > limit else "")


def _args_summary(args: dict, secrets: tuple[str, ...] = ()) -> str:
    """<=120 chars, one line, with secret-looking arg values redacted."""
    parts = []
    for k, v in args.items():
        if _SECRET_KEY_RE.search(str(k)):
            parts.append(f"{k}=***")
        else:
            parts.append(f"{k}={_scrub_value(v, secrets)}")
    s = ", ".join(parts)
    return s[:_MAX_SUMMARY]


def _clean_detail(
    value: Any, secrets: tuple[str, ...] = (), limit: int = _MAX_DETAIL
) -> str:
    """One-line, length-capped detail string for trace events."""
    s = redact_secret_values(value, secrets).replace("\r", " ").replace("\n", " ")
    return s[:limit] + ("..." if len(s) > limit else "")


def _truncate_output(
    out: Any, secrets: tuple[str, ...] = (), limit: int = _MAX_OUTPUT
) -> str:
    """Cap sandbox output for LLM consumption."""
    s = redact_secret_values(out, secrets)
    if len(s) <= limit:
        return s
    return s[:limit] + f"\n... [truncated {len(s) - limit} chars]"


def _get_handle(ctx: RunContext, sandbox_id: Any) -> SandboxHandle:
    h = ctx.sandbox_handles.get(str(sandbox_id))
    if h is None:
        raise ValueError("unknown or inactive sandbox handle")
    return h


def env_prelude(code: str, env: dict, entitled_names: set[str] | frozenset[str]) -> str:
    """Inject only explicitly referenced values authorized for one candidate."""
    referenced = set(_ENV_REFERENCE_RE.findall(code))
    selected = {
        key: value
        for key, value in env.items()
        if key in referenced and key in entitled_names
    }
    if not selected:
        return code
    return "import os as _os\n_os.environ.update(" + repr(selected) + ")\n" + code


def _resolve_within(root: str, candidate: str) -> Path:
    if not root:
        raise ValueError("dataset upload root is not configured")
    root_path = Path(root).resolve(strict=True)
    candidate_path = Path(candidate).resolve(strict=True)
    try:
        candidate_path.relative_to(root_path)
    except ValueError as exc:
        raise ValueError("local_dir must be within the configured dataset root") from exc
    if not candidate_path.is_dir():
        raise ValueError("local_dir must be a directory")
    return candidate_path


def cleanup_run_context(ctx: RunContext) -> None:
    """Destroy all remote resources owned by a run and invalidate its handles."""
    handles = list(ctx.sandbox_handles.values())
    ctx.sandbox_handles.clear()
    for handle in handles:
        try:
            ctx.pool.release(handle)
        except Exception:
            # destroy_all retries any sandbox that release could not delete.
            pass
    ctx.pool.destroy_all()


def append_result_record(ctx: RunContext, record: dict) -> None:
    """Append exactly one candidate/document record for the current run."""
    validate_result_record(ctx, record)
    key = (str(record.get("candidate", "")), str(record.get("doc_id", "")))
    if not all(key):
        raise ValueError("candidate and doc_id must be non-empty")
    path = Path(ctx.results_path)
    with ctx.result_lock:
        if not ctx.results_initialized:
            if path.exists():
                for line_number, line in enumerate(
                    path.read_text(encoding="utf-8").splitlines(), start=1
                ):
                    if not line.strip():
                        continue
                    try:
                        existing = json.loads(line)
                        validate_result_record(ctx, existing)
                        existing_key = (
                            str(existing["candidate"]), str(existing["doc_id"])
                        )
                    except (KeyError, TypeError, json.JSONDecodeError) as exc:
                        raise ValueError(
                            "invalid existing result record"
                        ) from exc
                    if existing_key in ctx.result_keys:
                        raise ValueError("duplicate result record")
                    ctx.result_keys.add(existing_key)
            ctx.results_initialized = True
        if key in ctx.result_keys:
            raise ValueError("duplicate result record")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
        ctx.result_keys.add(key)


def validate_result_record(ctx: RunContext, record: dict) -> None:
    """Enforce result capabilities and bounded frozen-schema values."""
    if not isinstance(record, dict):
        raise ValueError("invalid result record")
    candidate = record.get("candidate")
    doc_id = record.get("doc_id")
    if (
        not isinstance(candidate, str)
        or not isinstance(doc_id, str)
        or not candidate
        or not doc_id
        or len(candidate) > MAX_RESULT_ID_CHARS
        or len(doc_id) > MAX_RESULT_ID_CHARS
    ):
        raise ValueError("invalid result capability")
    if candidate not in ctx.allowed_candidate_names or doc_id not in ctx.allowed_doc_ids:
        raise ValueError("result is outside the current run capability")
    if not isinstance(record.get("ok"), bool):
        raise ValueError("invalid result status")
    try:
        latency = float(record.get("latency_s", 0.0))
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid result latency") from exc
    if latency < 0 or not math.isfinite(latency):
        raise ValueError("invalid result latency")
    if record["ok"]:
        prediction = record.get("prediction")
        required = {"invoice_number", "date", "vendor", "total"}
        if not isinstance(prediction, dict) or set(prediction) != required:
            raise ValueError("invalid result prediction schema")
        if any(
            not isinstance(value, str) or len(value) > MAX_RESULT_FIELD_CHARS
            for value in prediction.values()
        ):
            raise ValueError("result field exceeds the allowed size")
        if record.get("error") is not None:
            raise ValueError("successful result cannot include an error")
    else:
        error = record.get("error")
        if record.get("prediction") is not None:
            raise ValueError("failed result cannot include a prediction")
        if not isinstance(error, str) or len(error) > MAX_RESULT_ERROR_CHARS:
            raise ValueError("invalid result error")
    if len(json.dumps(record, ensure_ascii=False).encode("utf-8")) > MAX_RESULT_RECORD_BYTES:
        raise ValueError("result record exceeds the allowed size")


# ---------------------------------------------------------------------------
# Tool implementations (lazy sibling imports — see module docstring)
# ---------------------------------------------------------------------------


# Google's first page is the popular answer by construction, so five results is
# a shortlist of whatever is famous. Niche tools sit at ranks 10-30, which is
# two or three pages in — cheap here, because SERP is billed per request and
# pages are fetched concurrently.
WEB_SEARCH_RESULTS = 25


def _tool_web_search(args: dict, ctx: RunContext) -> str:
    from engine import docs_intel

    return json.dumps(docs_intel.web_search(
        args["query"], n=WEB_SEARCH_RESULTS, env=ctx.runtime_env))


def _tool_scrape_docs(args: dict, ctx: RunContext) -> str:
    from engine import docs_intel

    url = args["url"]
    content = docs_intel.scrape_page(url, env=ctx.runtime_env)
    # Remember every scraped URL as a citation for the final report (§10).
    safe_url = redact_secret_values(url, _secret_values(ctx))
    if not any(c.get("url") == safe_url for c in ctx.citations):
        ctx.citations.append({"title": safe_url, "url": safe_url})
    return json.dumps(content)


def _tool_generate_adapter(args: dict, ctx: RunContext) -> str:
    from engine import adapter_gen

    tool_name = args["tool_name"]
    if tool_name not in ctx.allowed_candidate_names:
        raise ValueError("tool_name is not a candidate in the current run")
    candidate = adapter_gen.generate_adapter(
        tool_name, args["docs_md"], env=ctx.runtime_env
    )
    replace_candidate(ctx, tool_name, candidate)
    return json.dumps(asdict(candidate))


def _tool_spawn_sandbox(args: dict, ctx: RunContext) -> str:
    label = str(args["label"])
    if label not in ctx.allowed_candidate_names:
        raise ValueError("sandbox label is not a candidate in the current run")
    handle = ctx.pool.acquire(label)
    ctx.sandbox_handles[str(handle.id)] = handle
    return json.dumps({"id": handle.id, "label": handle.label})


def _tool_exec_in_sandbox(args: dict, ctx: RunContext) -> str:
    handle = _get_handle(ctx, args["id"])
    out = ctx.pool.exec(handle, args["cmd"], timeout=120)
    return json.dumps({"output": _truncate_output(out, _secret_values(ctx))})


def _tool_run_python_in_sandbox(args: dict, ctx: RunContext) -> str:
    handle = _get_handle(ctx, args["id"])
    code = env_prelude(
        args["code"],
        ctx.env_passthrough,
        frozenset(),
    )
    out = ctx.pool.run_python(handle, code, timeout=180)
    return json.dumps({"output": _truncate_output(out, _secret_values(ctx))})


def _tool_upload_files(args: dict, ctx: RunContext) -> str:
    handle = _get_handle(ctx, args["id"])
    local_dir = _resolve_within(ctx.allowed_dataset_root, args["local_dir"])
    uploaded = []
    for root, _dirs, files in os.walk(local_dir):
        for fname in sorted(files):
            local_path = Path(root, fname).resolve(strict=True)
            try:
                local_path.relative_to(local_dir)
            except ValueError as exc:
                raise ValueError("dataset contains a file outside its root") from exc
            # Remote path is relative to the sandbox workdir (§1: CWD holds
            # the dataset), preserving subdirectory structure.
            rel = os.path.relpath(str(local_path), str(local_dir)).replace(os.sep, "/")
            ctx.pool.upload(handle, str(local_path), rel)
            uploaded.append(rel)
    return json.dumps({"uploaded": uploaded, "count": len(uploaded)})


def _tool_record_result(args: dict, ctx: RunContext) -> str:
    candidate = str(args.get("candidate", ""))
    doc_id = str(args.get("doc_id", ""))
    if candidate not in ctx.allowed_candidate_names:
        raise ValueError("candidate is not part of the current run")
    if doc_id not in ctx.allowed_doc_ids:
        raise ValueError("doc_id is not part of the current dataset")
    ok = args.get("ok")
    # Shape exactly per §2; on failure prediction is null and error is a string.
    record = {
        "candidate": candidate,
        "doc_id": doc_id,
        "ok": ok,
        "prediction": args.get("prediction") if ok is True else None,
        "latency_s": args.get("latency_s", 0.0),
        "error": None if ok is True else args.get("error", "unknown error"),
    }
    record = json.loads(
        redact_secret_values(json.dumps(record), _secret_values(ctx))
    )
    append_result_record(ctx, record)
    return json.dumps(record)


def _tool_evaluate(args: dict, ctx: RunContext) -> str:
    from engine.evaluate import evaluate_results

    if not ctx.ground_truth_path or not ctx.allowed_dataset_root:
        raise ValueError("evaluation dataset is not configured for this run")
    requested_results = Path(args.get("results_path", "")).resolve()
    requested_ground_truth = Path(args.get("ground_truth_path", "")).resolve()
    if requested_results != Path(ctx.results_path).resolve():
        raise ValueError("results_path does not match the current run")
    if requested_ground_truth != Path(ctx.ground_truth_path).resolve():
        raise ValueError("ground_truth_path does not match the current dataset")
    ground_truth = Path(ctx.ground_truth_path).resolve(strict=True)
    dataset_root = Path(ctx.allowed_dataset_root).resolve(strict=True)
    try:
        ground_truth.relative_to(dataset_root)
    except ValueError as exc:
        raise ValueError("ground truth is outside the current dataset") from exc
    metrics = evaluate_results(ctx.results_path, str(ground_truth))
    ctx.evaluated_metrics = metrics
    return json.dumps(metrics)


def _tool_write_report(args: dict, ctx: RunContext) -> str:
    from engine import report_gen

    if ctx.evaluated_metrics is None:
        raise ValueError("evaluate must succeed before write_report")
    secrets = _secret_values(ctx)
    metrics = redact_data(ctx.evaluated_metrics, secrets)
    citations = redact_data(ctx.citations, secrets)
    out_path = os.path.join(ctx.run_dir, "report.md")
    markdown = report_gen.write_report(
        metrics, citations, os.devnull, env=ctx.runtime_env
    )
    markdown = redact_secret_values(markdown, secrets)
    with open(out_path, "w", encoding="utf-8") as handle:
        handle.write(markdown)
    return json.dumps({"report_path": out_path, "markdown": markdown})


_HANDLERS: dict[str, Callable[[dict, RunContext], str]] = {
    "web_search": _tool_web_search,
    "scrape_docs": _tool_scrape_docs,
    "generate_adapter": _tool_generate_adapter,
    "spawn_sandbox": _tool_spawn_sandbox,
    "exec_in_sandbox": _tool_exec_in_sandbox,
    "run_python_in_sandbox": _tool_run_python_in_sandbox,
    "upload_files": _tool_upload_files,
    "evaluate": _tool_evaluate,
    "write_report": _tool_write_report,
}


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def dispatch_tool(name: str, args: dict, ctx: RunContext) -> str:
    """Run one tool call. Always returns a JSON string (errors included).

    Emits a 'trace' artifact before (status='start') and after
    (status='ok'|'error') every call, per the frozen SSE schema (§11).
    """
    if isinstance(args, str):  # tolerate agents that pass raw JSON
        try:
            args = json.loads(args)
        except Exception:
            args = {}
    args = args or {}
    secrets = _secret_values(ctx)
    summary = _args_summary(args, secrets)

    ctx.emit("artifact", {"kind": "trace", "tool": name,
                          "args_summary": summary, "status": "start"})

    handler = _HANDLERS.get(name)
    if handler is None:
        detail = f"unknown tool: {name}"
        ctx.emit("artifact", {"kind": "trace", "tool": name,
                              "args_summary": summary, "status": "error",
                              "detail": detail})
        return json.dumps({"error": detail})

    try:
        result = handler(args, ctx)
    except Exception as e:
        detail = _clean_detail(f"{type(e).__name__}: {e}", secrets)
        ctx.emit("artifact", {"kind": "trace", "tool": name,
                              "args_summary": summary, "status": "error",
                              "detail": detail})
        return json.dumps({"error": detail})

    result = redact_secret_values(result, secrets)
    ctx.emit("artifact", {"kind": "trace", "tool": name,
                          "args_summary": summary, "status": "ok",
                          "detail": _clean_detail(result, secrets)})
    return result


# ---------------------------------------------------------------------------
# OpenAI function-calling schemas
# ---------------------------------------------------------------------------

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for extraction tools, libraries, docs, or pricing. Returns a JSON list of {title, url, snippet}.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scrape_docs",
            "description": "Scrape a documentation or pricing page and return its content (text or HTML). The URL is remembered as a citation for the final report.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Page URL to scrape."},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_adapter",
            "description": "Generate a sandbox adapter for the named tool from its documentation markdown. Returns the Candidate as JSON and stores it as the active candidate for that tool.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tool_name": {"type": "string", "description": "Unique slug, e.g. 'tesseract'."},
                    "docs_md": {"type": "string", "description": "Documentation markdown to build the adapter from."},
                },
                "required": ["tool_name", "docs_md"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "spawn_sandbox",
            "description": "Acquire an isolated sandbox with the given label. Returns its sandbox id for use with the other sandbox tools.",
            "parameters": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "description": "Human-readable label, usually the candidate name."},
                },
                "required": ["label"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "exec_in_sandbox",
            "description": "Run a shell command in a sandbox (120s timeout, e.g. build/install steps). Output is truncated to 4000 chars.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Sandbox id from spawn_sandbox."},
                    "cmd": {"type": "string", "description": "Shell command to run."},
                },
                "required": ["id", "cmd"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_python_in_sandbox",
            "description": "Run untrusted Python code in a sandbox (180s timeout) without provider credentials. Output is redacted and truncated to 4000 chars.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Sandbox id from spawn_sandbox."},
                    "code": {"type": "string", "description": "Python source to execute."},
                },
                "required": ["id", "code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "upload_files",
            "description": "Recursively upload files from the configured dataset root into the sandbox workdir, preserving relative paths.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Sandbox id from spawn_sandbox."},
                    "local_dir": {"type": "string", "description": "Local directory to walk and upload."},
                },
                "required": ["id", "local_dir"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "evaluate",
            "description": "Compute deterministic metrics using the current run's capability-bound results and ground-truth paths. Supplied paths must match those capabilities.",
            "parameters": {
                "type": "object",
                "properties": {
                    "results_path": {"type": "string", "description": "Path to the results JSONL file."},
                    "ground_truth_path": {"type": "string", "description": "Path to ground_truth.csv."},
                },
                "required": ["results_path", "ground_truth_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_report",
            "description": "Write the final ranked markdown report (with citations collected from scrape_docs) to <run_dir>/report.md and return it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "metrics_json": {"type": "string", "description": "Metrics JSON string exactly as returned by the evaluate tool."},
                },
                "required": ["metrics_json"],
            },
        },
    },
]
