"""Agent tool layer for ProofBench (CONTRACTS §8).

Exposes TOOL_SCHEMAS (OpenAI function-calling format) and dispatch_tool().
Sibling engine modules (docs_intel, adapter_gen, evaluate, report_gen,
sandbox_pool) are imported lazily inside the tool functions so this module
always imports cleanly, even while other lanes' files are mid-write.

All return values are JSON strings suitable for tool-role chat messages.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Sandbox handles live here, keyed by handle id, so the agent only ever
# passes opaque id strings between tool calls.
_SANDBOX_HANDLES: dict[str, SandboxHandle] = {}

_SECRET_KEY_RE = re.compile(r"(?i)(key|token|pass|secret)")

_MAX_SUMMARY = 120
_MAX_DETAIL = 200  # SSE log lines must be single-line, <=300 chars (§14)
_MAX_OUTPUT = 4000


def _scrub_value(value: Any, limit: int = 60) -> str:
    """One-line, length-capped rendering of an arg value for the trace feed."""
    s = value if isinstance(value, str) else repr(value)
    s = s.replace("\r", " ").replace("\n", " ")
    return s[:limit] + ("..." if len(s) > limit else "")


def _args_summary(args: dict) -> str:
    """<=120 chars, one line, with secret-looking arg values redacted."""
    parts = []
    for k, v in args.items():
        if _SECRET_KEY_RE.search(str(k)):
            parts.append(f"{k}=***")
        else:
            parts.append(f"{k}={_scrub_value(v)}")
    s = ", ".join(parts)
    return s[:_MAX_SUMMARY]


def _clean_detail(value: Any, limit: int = _MAX_DETAIL) -> str:
    """One-line, length-capped detail string for trace events."""
    s = str(value).replace("\r", " ").replace("\n", " ")
    return s[:limit] + ("..." if len(s) > limit else "")


def _truncate_output(out: Any, limit: int = _MAX_OUTPUT) -> str:
    """Cap sandbox output for LLM consumption."""
    s = "" if out is None else str(out)
    if len(s) <= limit:
        return s
    return s[:limit] + f"\n... [truncated {len(s) - limit} chars]"


def _get_handle(sandbox_id: Any) -> SandboxHandle:
    h = _SANDBOX_HANDLES.get(str(sandbox_id))
    if h is None:
        raise ValueError(f"unknown sandbox id {sandbox_id!r}; call spawn_sandbox first")
    return h


# ---------------------------------------------------------------------------
# Tool implementations (lazy sibling imports — see module docstring)
# ---------------------------------------------------------------------------


def _tool_web_search(args: dict, ctx: RunContext) -> str:
    from engine import docs_intel

    return json.dumps(docs_intel.web_search(args["query"]))


def _tool_scrape_docs(args: dict, ctx: RunContext) -> str:
    from engine import docs_intel

    url = args["url"]
    content = docs_intel.scrape_page(url)
    # Remember every scraped URL as a citation for the final report (§10).
    if not any(c.get("url") == url for c in ctx.citations):
        ctx.citations.append({"title": url, "url": url})
    return json.dumps(content)


def _tool_generate_adapter(args: dict, ctx: RunContext) -> str:
    from engine import adapter_gen

    tool_name = args["tool_name"]
    candidate = adapter_gen.generate_adapter(tool_name, args["docs_md"], env=ctx.env_passthrough)
    ctx.candidates[tool_name] = candidate
    return json.dumps(asdict(candidate))


def _tool_spawn_sandbox(args: dict, ctx: RunContext) -> str:
    handle = ctx.pool.acquire(args["label"])
    _SANDBOX_HANDLES[str(handle.id)] = handle
    return json.dumps({"id": handle.id, "label": handle.label})


def _tool_exec_in_sandbox(args: dict, ctx: RunContext) -> str:
    handle = _get_handle(args["id"])
    out = ctx.pool.exec(handle, args["cmd"], timeout=120)
    return json.dumps({"output": _truncate_output(out)})


def _tool_run_python_in_sandbox(args: dict, ctx: RunContext) -> str:
    handle = _get_handle(args["id"])
    code = args["code"]
    if ctx.env_passthrough:
        # Bake the run's secrets into the sandbox process env (§6/§8). The
        # prelude is never included in args_summary or trace details.
        code = (
            "import os as _os\n_os.environ.update("
            + repr(dict(ctx.env_passthrough))
            + ")\n"
            + code
        )
    out = ctx.pool.run_python(handle, code, timeout=180)
    return json.dumps({"output": _truncate_output(out)})


def _tool_upload_files(args: dict, ctx: RunContext) -> str:
    handle = _get_handle(args["id"])
    local_dir = args["local_dir"]
    uploaded = []
    for root, _dirs, files in os.walk(local_dir):
        for fname in sorted(files):
            local_path = os.path.join(root, fname)
            # Remote path is relative to the sandbox workdir (§1: CWD holds
            # the dataset), preserving subdirectory structure.
            rel = os.path.relpath(local_path, local_dir).replace(os.sep, "/")
            ctx.pool.upload(handle, local_path, rel)
            uploaded.append(rel)
    return json.dumps({"uploaded": uploaded, "count": len(uploaded)})


def _tool_record_result(args: dict, ctx: RunContext) -> str:
    ok = bool(args.get("ok"))
    # Shape exactly per §2; on failure prediction is null and error is a string.
    record = {
        "candidate": str(args.get("candidate", "")),
        "doc_id": str(args.get("doc_id", "")),
        "ok": ok,
        "prediction": args.get("prediction") if ok else None,
        "latency_s": float(args.get("latency_s") or 0.0),
        "error": None if ok else str(args.get("error") or "unknown error"),
    }
    path = ctx.results_path
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return json.dumps(record)


def _tool_evaluate(args: dict, ctx: RunContext) -> str:
    from engine.evaluate import evaluate_results

    metrics = evaluate_results(args["results_path"], args["ground_truth_path"])
    return json.dumps(metrics)


def _tool_write_report(args: dict, ctx: RunContext) -> str:
    from engine import report_gen

    metrics = args["metrics_json"]
    if isinstance(metrics, str):
        metrics = json.loads(metrics)
    out_path = os.path.join(ctx.run_dir, "report.md")
    markdown = report_gen.write_report(metrics, ctx.citations, out_path)
    return json.dumps({"report_path": out_path, "markdown": markdown})


_HANDLERS: dict[str, Callable[[dict, RunContext], str]] = {
    "web_search": _tool_web_search,
    "scrape_docs": _tool_scrape_docs,
    "generate_adapter": _tool_generate_adapter,
    "spawn_sandbox": _tool_spawn_sandbox,
    "exec_in_sandbox": _tool_exec_in_sandbox,
    "run_python_in_sandbox": _tool_run_python_in_sandbox,
    "upload_files": _tool_upload_files,
    "record_result": _tool_record_result,
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
    summary = _args_summary(args)

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
        detail = _clean_detail(f"{type(e).__name__}: {e}")
        ctx.emit("artifact", {"kind": "trace", "tool": name,
                              "args_summary": summary, "status": "error",
                              "detail": detail})
        return json.dumps({"error": detail})

    ctx.emit("artifact", {"kind": "trace", "tool": name,
                          "args_summary": summary, "status": "ok",
                          "detail": _clean_detail(result)})
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
            "description": "Run Python code in a sandbox (180s timeout). The sandbox environment is preloaded with the run's secrets, so adapter code may read them via os.environ. Output is truncated to 4000 chars.",
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
            "description": "Recursively upload every file under a local directory into the sandbox workdir, preserving relative paths.",
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
            "name": "record_result",
            "description": "Append one result record (one candidate x one document) to the results JSONL file, per the frozen results schema.",
            "parameters": {
                "type": "object",
                "properties": {
                    "candidate": {"type": "string", "description": "Candidate slug."},
                    "doc_id": {"type": "string", "description": "Document id, e.g. 'inv_001'."},
                    "ok": {"type": "boolean", "description": "Whether extraction succeeded."},
                    "prediction": {
                        "type": "object",
                        "description": "Extracted fields; omit on failure.",
                        "properties": {
                            "invoice_number": {"type": "string"},
                            "date": {"type": "string"},
                            "vendor": {"type": "string"},
                            "total": {"type": "string"},
                        },
                    },
                    "latency_s": {"type": "number", "description": "Measured latency in seconds."},
                    "error": {"type": "string", "description": "Error string on failure; omit when ok."},
                },
                "required": ["candidate", "doc_id", "ok"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "evaluate",
            "description": "Compute deterministic metrics (exact accuracy, field F1, CER, latency, failure rate) for a results JSONL file against a ground-truth CSV.",
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
