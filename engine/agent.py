"""Orchestrator agent (CONTRACTS §9).

Two modes:
- chat(): conversational INTAKE/DISCOVERY — proposes an editable benchmark spec.
- run_benchmark(): autonomous LLM tool-calling loop over the full protocol.
  Falls back to run_benchmark_scripted() (deterministic, same building blocks)
  if the loop derails — the demo must never die.

The agent decides logistics. It NEVER judges extraction correctness — scoring
happens only in engine.evaluate (deterministic, ground truth based).
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from engine.candidates.base import Candidate, RESULT_JSON_WRAPPER
from engine.sandbox_pool import SandboxPool
from engine.tools import TOOL_SCHEMAS, RunContext, dispatch_tool

KIMI_BASE_URL = "https://api.moonshot.ai/v1"
MAX_TOOL_CALLS = 40
FIELDS = ["invoice_number", "date", "vendor", "total"]
CHAT_TOOLS = {"web_search", "scrape_docs"}

INTAKE_SYSTEM = """You are ProofBench's Real-mode intake agent. The user wants to compare
any company tools or services, not only OCR or document-extraction products.

Your job in this conversation:
1. Understand what category of tools they want to compare.
2. Capture the company's implementation objective and important constraints.
3. If they named specific tools, find each official implementation guide. If not, use
   web_search to find 3-5 strong candidates, then scrape_docs on the most promising
   official documentation pages. Prefer primary vendor docs over reviews.
3. When you have enough, propose the benchmark spec as a fenced ```json block
   with EXACTLY this shape:
   {"benchmark_type": "tool_assessment",
    "category": str,
    "objective": str,
    "candidates": [{"name": slug, "display_name": str, "docs_url": str,
                    "pricing_url": str, "kind": "local_tool"|"hosted_api"|"saas"}]}
4. Every candidate must have a real docs_url from search or scraped evidence. Do not use
   the built-in OCR candidates unless the user explicitly asks for OCR.
5. Keep replies concise and concrete. Explain that implementation is attempted only when
   the docs are sufficient; otherwise Daytona is skipped and the tool receives a rating."""

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
    """Resolve the configured orchestrator, defaulting to the available key."""
    env = env or os.environ
    configured = env.get("ORCHESTRATOR_PROVIDER", "auto").strip().casefold()
    if configured in {"kimi", "moonshot"}:
        return "moonshot"
    if configured == "openai":
        return "openai"
    return "moonshot" if env.get("MOONSHOT_API_KEY", "").strip() else "openai"


def _orchestrator_client(env: dict | None = None):
    from openai import OpenAI

    env = env or os.environ
    if _orchestrator_provider(env) == "moonshot":
        return OpenAI(
            base_url=KIMI_BASE_URL,
            api_key=env["MOONSHOT_API_KEY"],
        )
    return OpenAI(api_key=env["OPENAI_API_KEY"])


def _orchestrator_model(env: dict | None = None) -> str:
    env = env or os.environ
    if _orchestrator_provider(env) == "moonshot":
        return env.get("KIMI_MODEL", "kimi-k2-thinking")
    return env.get("OPENAI_ORCHESTRATOR_MODEL", "gpt-4o")


class Orchestrator:
    def __init__(self, run_id: str, run_dir: str, emit, cancel_event=None, provider_env=None):
        self.run_id = run_id
        self.run_dir = run_dir
        self.emit = emit
        self.cancel_event = cancel_event
        os.makedirs(run_dir, exist_ok=True)
        self.results_path = os.path.join(run_dir, "results.jsonl")
        self.provider_env = dict(provider_env or {})
        self.runtime_env = dict(os.environ)
        self.runtime_env.update(self.provider_env)
        self.pool = SandboxPool(size=4)
        self.ctx = RunContext(
            run_id=run_id,
            run_dir=run_dir,
            pool=self.pool,
            emit=emit,
            results_path=self.results_path,
        )
        self.ctx.env_passthrough = self.provider_env or {
            k: os.environ[k]
            for k in ("NOSANA_BASE_URL", "NOSANA_API_KEY", "NOSANA_MODEL", "DOUBLEWORD_BASE_URL", "DOUBLEWORD_API_KEY", "DOUBLEWORD_MODEL", "OPENAI_API_KEY", "OPENAI_VISION_MODEL")
            if os.environ.get(k)
        }
        self._lock = threading.Lock()          # guards results.jsonl appends
        self._handle_to_candidate: dict[str, str] = {}
        self._messages: list[dict] = []

    # ------------------------------------------------------------------ events
    def _delta(self, text: str) -> None:
        self.emit("delta", {"text": text})

    def _state(self, phase: str, candidates: dict | None = None) -> None:
        self.emit("state", {"phase": phase, "candidates": candidates or {}})

    def _check_cancelled(self) -> None:
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise RuntimeError("run stopped by user")

    # ------------------------------------------------------------------ chat
    def chat(self, user_message: str) -> None:
        """INTAKE/DISCOVERY conversation; emits deltas and eventually a spec artifact."""
        if not self._messages:
            self._messages = [{"role": "system", "content": INTAKE_SYSTEM}]
        self._messages.append({"role": "user", "content": user_message})
        client = _orchestrator_client(self.runtime_env)
        schemas = [s for s in TOOL_SCHEMAS if s["function"]["name"] in CHAT_TOOLS]

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
                        {"role": "tool", "tool_call_id": tc.id, "content": result}
                    )
                continue

            text = msg.content or ""
            self._messages.append({"role": "assistant", "content": text})
            self._delta(text)
            spec = self._extract_spec(text)
            if spec:
                self.emit("artifact", {"kind": "spec", "spec": spec})
                self._state("SPEC_CONFIRM")
            return

        self._delta("I'm going in circles — please rephrase what you'd like to benchmark.")

    @staticmethod
    def _extract_spec(text: str) -> dict | None:
        m = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
        if not m:
            return None
        try:
            spec = json.loads(m.group(1))
        except json.JSONDecodeError:
            return None
        if isinstance(spec, dict) and spec.get("candidates"):
            spec.setdefault("benchmark_type", "tool_assessment")
            return spec
        return None

    # ------------------------------------------------------------- LLM run mode
    def run_benchmark(self, spec: dict) -> dict:
        """Autonomous protocol driven by Kimi tool-calling, with scripted fallback."""
        self._active_spec = spec
        if spec.get("benchmark_type") == "tool_assessment":
            return self.run_tool_assessment(spec)
        self._state("DOCS_INTEL", {c["name"]: "pending" for c in spec["candidates"]})
        dataset = spec["dataset"]["path"]
        ground_truth = os.path.join(dataset, "ground_truth.csv")
        images = self._list_images(dataset)
        if not images:
            raise RuntimeError(f"no images found in {dataset}/images")

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

        client = _orchestrator_client()
        calls = 0
        consecutive_errors = 0
        finished = False

        while calls < MAX_TOOL_CALLS and not finished:
            self._check_cancelled()
            resp = client.chat.completions.create(
                model=_orchestrator_model(), messages=messages, tools=TOOL_SCHEMAS
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
                    result = json.dumps({"error": f"{type(e).__name__}: {e}"})
                    consecutive_errors += 1
                self._track_phase(name, args)
                messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": result}
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
        """Assess arbitrary tools from docs and use Daytona only for viable integrations."""
        from engine.tool_assessment import (
            assess_documentation_batch,
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
                for citation in self.ctx.citations:
                    if citation.get("url") == docs_url:
                        citation["title"] = f"{display_name} documentation"
                scraped_candidates.append({"name": name, "docs_text": docs_text})
                statuses[name] = "queued"
                self._state("DOCS_INTEL", dict(statuses))
            except Exception as exc:
                metrics[name] = unavailable_result(f"Documentation scrape failed: {type(exc).__name__}: {exc}")
                statuses[name] = "skipped"
                self.emit("artifact", {
                    "kind": "trace",
                    "tool": "scrape_docs",
                    "args_summary": name,
                    "status": "error",
                    "detail": f"{type(exc).__name__}: {exc}"[:200],
                })
                self._state("DOCS_INTEL", dict(statuses))
                continue

        assessments: dict[str, dict] = {}
        if scraped_candidates:
            self._state("ADAPTER_GEN", {
                **statuses,
                **{candidate["name"]: "batching" for candidate in scraped_candidates},
            })
            model = self.runtime_env.get("DOUBLEWORD_MODEL", "deepseek-ai/DeepSeek-V4-Pro")
            self.emit("artifact", {
                "kind": "trace",
                "tool": "doubleword_autobatcher",
                "args_summary": f"{len(scraped_candidates)} implementation assessments with {model}",
                "status": "start",
            })
            self._delta(
                f"Submitting {len(scraped_candidates)} documentation assessments to Doubleword as one batch.\n"
            )
            try:
                assessments = assess_documentation_batch(
                    scraped_candidates,
                    objective,
                    env=self.runtime_env,
                )
                failures = sum(1 for result in assessments.values() if result.get("error"))
                self.emit("artifact", {
                    "kind": "trace",
                    "tool": "doubleword_autobatcher",
                    "args_summary": f"{len(scraped_candidates)} implementation assessments with {model}",
                    "status": "ok" if failures < len(scraped_candidates) else "error",
                    "detail": f"{len(scraped_candidates) - failures} completed, {failures} failed",
                })
            except Exception as exc:
                assessments = {
                    candidate["name"]: {"error": f"{type(exc).__name__}: {exc}"}
                    for candidate in scraped_candidates
                }
                self.emit("artifact", {
                    "kind": "trace",
                    "tool": "doubleword_autobatcher",
                    "args_summary": f"{len(scraped_candidates)} implementation assessments with {model}",
                    "status": "error",
                    "detail": f"{type(exc).__name__}: {exc}"[:200],
                })

        for scraped_candidate in scraped_candidates:
            self._check_cancelled()
            name = scraped_candidate["name"]
            candidate_spec = candidate_by_name[name]
            display_name = str(candidate_spec.get("display_name") or name)
            assessment = assessments.get(name) or {"error": "Doubleword returned no assessment"}
            if assessment.get("error"):
                metrics[name] = unavailable_result(
                    f"Doubleword assessment failed: {assessment['error']}"
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
                "detail": plan["reason"][:200],
            })
            if not plan["implementable"]:
                metrics[name] = result_from_plan(plan, "not_implementable", False)
                statuses[name] = "rated"
                self._state("EVALUATING", dict(statuses))
                self._delta(f"{display_name}: documentation was insufficient for a credible implementation. Daytona skipped.\n")
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
                if self.ctx.env_passthrough:
                    code = "import os\nos.environ.update(" + repr(self.ctx.env_passthrough) + ")\n" + code
                output = self.pool.run_python(handle, code, timeout=180)
                verification_status = "passed" if "PROOFBENCH_OK" in output else "failed"
                self._log(name, f"implementation verification: {verification_status}", "validating")
            except Exception as exc:
                self._log(name, f"implementation verification failed: {type(exc).__name__}: {exc}"[:300], "failed")
                verification_status = "failed"
            finally:
                if handle is not None:
                    self.pool.release(handle)

            metrics[name] = result_from_plan(plan, verification_status, True)
            statuses[name] = "done" if verification_status == "passed" else "failed"
            self._state("EVALUATING", dict(statuses))

        self._state("EVALUATING", dict(statuses))
        metrics_path = os.path.join(self.run_dir, "metrics.json")
        with open(metrics_path, "w", encoding="utf-8") as handle:
            json.dump(metrics, handle, indent=2)
        self.emit("artifact", {"kind": "results", "metrics": metrics})

        self._state("REPORTING", dict(statuses))
        report = write_assessment_report(
            metrics,
            self.ctx.citations,
            os.path.join(self.run_dir, "report.md"),
        )
        from engine.pdf_report import write_pdf_report

        write_pdf_report(metrics, report, os.path.join(self.run_dir, "report.pdf"))
        self.emit("artifact", {
            "kind": "report",
            "markdown": report,
            "citations": self.ctx.citations,
        })
        self._state("DONE", dict(statuses))
        return metrics

    # --------------------------------------------------------- scripted run mode
    def run_benchmark_scripted(self, spec: dict) -> dict:
        """Deterministic pipeline — same building blocks, no LLM in the loop."""
        self._active_spec = spec
        dataset = spec["dataset"]["path"]
        ground_truth = os.path.join(dataset, "ground_truth.csv")
        images = self._list_images(dataset)
        if not images:
            raise RuntimeError(f"no images found in {dataset}/images")
        self._state("PROVISIONING", {c["name"]: "pending" for c in spec["candidates"]})
        self.pool.start()
        self._run_candidates_scripted(spec["candidates"], images)
        return self._evaluate_and_report(ground_truth)

    def _run_candidates_scripted(self, candidates: list[dict], images: list[str]) -> None:
        with ThreadPoolExecutor(max_workers=4) as ex:
            list(ex.map(self._candidate_pipeline, candidates))

    # ------------------------------------------------------------- pipeline blocks
    def _candidate_pipeline(self, cand_spec: dict) -> None:
        """Build → validate (repair once → fallback) → run dataset, for one candidate."""
        name = cand_spec["name"]
        try:
            candidate = self._resolve_candidate(cand_spec)
            if candidate is None:
                self._state("RUNNING", {name: "failed"})
                return
            handle = self.pool.acquire(name)
            self._handle_to_candidate[handle.id] = name
            try:
                self._upload_dataset(handle)
                self._state("BUILDING", {name: "building"})
                self._build(handle, candidate)
                self._state("VALIDATING", {name: "validating"})
                if not self._validate(handle, candidate):
                    fb = self._try_fallback(cand_spec)
                    if fb is None:
                        self._state("RUNNING", {name: "failed"})
                        return
                    candidate = fb
                    self._build(handle, candidate)
                    if not self._validate(handle, candidate):
                        self._state("RUNNING", {name: "failed"})
                        return
                self._state("RUNNING", {name: "running"})
                self._run_dataset(handle, candidate)
                self._state("RUNNING", {name: "done"})
            finally:
                self.pool.release(handle)
        except Exception as e:
            self.emit(
                "artifact",
                {"kind": "trace", "tool": "pipeline", "args_summary": name,
                 "status": "error", "detail": f"{type(e).__name__}: {e}"[:200]},
            )
            self._state("RUNNING", {name: "failed"})

    def _upload_dataset(self, handle) -> None:
        """Upload images + ground truth into the sandbox (real sandboxes start empty)."""
        dataset = self._dataset_path
        if not dataset:
            return
        images = self._list_images(dataset)
        for img in images:
            self.pool.upload(handle, os.path.join(dataset, "images", img), f"images/{img}")
        gt = os.path.join(dataset, "ground_truth.csv")
        if os.path.exists(gt):
            self.pool.upload(handle, gt, "ground_truth.csv")
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
            repaired = self._repair_once(candidate, out)
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
        if not (self.ctx.env_passthrough.get("DOUBLEWORD_API_KEY") or os.environ.get("DOUBLEWORD_API_KEY")):
            return None
        try:
            from engine.adapter_gen import repair_adapter

            return repair_adapter(candidate.adapter_code, error_output[-2000:], env=self.ctx.env_passthrough)
        except AttributeError:
            return None  # adapter_gen has no repair_adapter; scripted fallback takes over
        except Exception:
            return None

    def _run_dataset(self, handle, candidate: Candidate) -> None:
        images = self._list_images(self._dataset_path)
        for img in images:
            self._check_cancelled()
            rel = f"images/{img}"
            code = self._adapter_code(candidate, rel)
            out = self.pool.run_python(handle, code, timeout=180)
            self._collate(out, candidate.name, doc_id=os.path.splitext(img)[0])
            self._log(handle.label, f"ran {rel}", "running")

    # --------------------------------------------------------------- collation
    def _dispatch_with_collation(self, name: str, args: dict) -> str:
        result = dispatch_tool(name, args, self.ctx)
        if name == "spawn_sandbox":
            try:
                info = json.loads(result)
                hid = info.get("id", "")
                self._handle_to_candidate[hid] = args.get("label", "")
                # the agent is told the dataset is "already uploaded" — make it true
                from engine import tools as _tools

                handle = _tools._SANDBOX_HANDLES.get(hid)
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
                    found.append(json.loads(line[len("RESULT_JSON:"):]))
                except json.JSONDecodeError:
                    continue
        return found

    def _collate(self, output: str, candidate: str, doc_id: str | None = None) -> None:
        for r in self._extract_result_lines(output):
            doc = r.get("doc_id") or r.get("image") or doc_id or "unknown"
            record = {
                "candidate": candidate,
                "doc_id": doc,
                "ok": bool(r.get("ok")),
                "prediction": r.get("fields") if r.get("ok") else None,
                "latency_s": r.get("latency_s", 0.0),
                "error": None if r.get("ok") else r.get("error", "unknown error"),
            }
            with self._lock:
                with open(self.results_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record) + "\n")

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

        pricing = {}
        pricing_path = os.path.join(self.run_dir, "pricing.json")
        if os.path.exists(pricing_path):
            with open(pricing_path, encoding="utf-8") as f:
                pricing = json.load(f)
        metrics = {}
        if os.path.exists(self.results_path):
            metrics = evaluate_results(self.results_path, ground_truth, pricing=pricing)
        if not metrics:
            from engine.demo_fallback import demo_metrics

            metrics = demo_metrics(
                getattr(self, "_active_spec", {"candidates": []}),
                n_docs=len(self._list_images(self._dataset_path)) or 15,
            )
        with open(os.path.join(self.run_dir, "metrics.json"), "w", encoding="utf-8") as f:
            json.dump(metrics, f, indent=2)
        self.emit("artifact", {"kind": "results", "metrics": metrics})

        self._state("REPORTING")
        if any(values.get("is_demo") for values in metrics.values()):
            from engine.demo_fallback import demo_report

            report = demo_report(metrics)
            with open(os.path.join(self.run_dir, "report.md"), "w", encoding="utf-8") as f:
                f.write(report)
        else:
            from engine.report_gen import write_report

            report = write_report(metrics, self.ctx.citations,
                                  os.path.join(self.run_dir, "report.md"))
        from engine.pdf_report import write_pdf_report

        write_pdf_report(metrics, report, os.path.join(self.run_dir, "report.pdf"))
        self.emit("artifact", {"kind": "report", "markdown": report,
                               "citations": self.ctx.citations})
        self._state("DONE")
        return metrics

    # ------------------------------------------------------------------ helpers
    _dataset_path: str = ""

    def _list_images(self, dataset: str) -> list[str]:
        self._dataset_path = dataset
        images_dir = os.path.join(dataset, "images")
        if not os.path.isdir(images_dir):
            return []
        return sorted(f for f in os.listdir(images_dir) if f.lower().endswith(".png"))

    def _first_image(self) -> str:
        images = self._list_images(self._dataset_path)
        return images[0] if images else "missing.png"

    def _adapter_code(self, candidate: Candidate, image_path: str) -> str:
        code = candidate.adapter_code
        if "RESULT_JSON:" not in code:
            code = code + "\n" + RESULT_JSON_WRAPPER
        env_patch = ""
        if self.ctx.env_passthrough:
            env_patch = (
                "import os\nos.environ.update("
                + json.dumps(self.ctx.env_passthrough)
                + ")\n"
            )
        argv_patch = f"import sys\nsys.argv = ['adapter', {image_path!r}]\n"
        return env_patch + argv_patch + code

    def _log(self, sandbox: str, line: str, phase: str) -> None:
        self.emit("artifact", {"kind": "sandbox_log", "sandbox": sandbox,
                               "line": line[:300], "phase": phase})
