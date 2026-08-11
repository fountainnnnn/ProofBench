"""Orchestrator agent (CONTRACTS §9).

Two modes:
- chat(): conversational INTAKE/DISCOVERY — a tool-using loop (web_search,
  scrape_docs) that proposes an editable benchmark spec and then stops for
  confirmation. Nothing executes until the user runs the spec.
- run_benchmark(): dispatches on benchmark_type. An extraction benchmark runs
  the deterministic pipeline (run_benchmark_scripted): the model may write an
  adapter from a candidate's documentation, but the engine alone invokes that
  adapter over every admitted image and writes evaluator input. A tool
  assessment runs run_tool_assessment, where the model plans and scores each
  candidate from documentation evidence.

Model judgement sits at the edges: what to compare, how to integrate it, and
how to describe the result. It NEVER judges extraction correctness — scoring
happens only in engine.evaluate (deterministic, ground truth based), which is
what makes a ProofBench number falsifiable.
"""

from __future__ import annotations

import copy
import ipaddress
import json
import os
import re
import secrets
import sys
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
MAX_CHAT_TOOL_RESULT_CHARS = 3_500
# Bounded, not open-ended: each attempt is one LLM repair against the sandbox's
# own error plus one re-validation. This is content, not control flow — the
# candidate pipeline around it stays exactly as scripted whether it takes 0
# repairs or MAX_ADAPTER_REPAIR_ATTEMPTS of them.
MAX_ADAPTER_REPAIR_ATTEMPTS = 3
FIELDS = ["invoice_number", "date", "vendor", "total"]
CHAT_TOOLS = {"web_search", "scrape_docs"}
_HISTORY_PATH = re.compile(
    r"(?:[A-Za-z]:[\\/](?![\\/])[^\s\"']*|file://[^\s\"']*|/(?:Users|home|root|app|tmp|var|etc|private|workspace)(?:[\\/][^\s\"']*)?)",
    re.IGNORECASE,
)
# Documentation fetches are IO-bound and independent per candidate. Four at a
# time matches the sandbox pipeline's width and stays well inside scrape
# providers' per-account concurrency, where a burst answers 429 rather than
# faster.
DOCS_CONCURRENCY = 8
# How many candidates execute at once. Each holds one sandbox, so the ceiling
# that matters is the provider's concurrent-memory budget, not this number;
# PROOFBENCH_SANDBOX_MEMORY_BUDGET_GIB queues anything beyond what the account
# Documents a hosted candidate processes at once inside its sandbox. Each one is
# an HTTP round trip, so this is bounded by provider rate limits, not by CPU.
HOSTED_DOC_CONCURRENCY = 8
# How much of a sandbox command's own output reaches the execution panel. This
# is the record of what actually ran, so it is generous; the session's event
# budget (PROOFBENCH_MAX_EVENTS) is the outer bound.
SANDBOX_LOG_LINES = 60


def _build_log_path(command: str) -> str:
    """A stable, readable filename for one build command's full output."""
    words = [w for w in re.split(r"[^A-Za-z0-9]+", str(command)) if w][:4]
    return "build-" + ("-".join(words).lower() or "command") + ".log"
SANDBOX_LOG_LINE_CHARS = 1000


def _schema_fields(spec_fields: list | None) -> list[dict]:
    """The run's schema as [{name, type}, ...], defaulting to the invoice set."""
    from engine.fields import parse_fields

    return [{"name": f.name, "type": f.type} for f in parse_fields(spec_fields)]


def _settled(fn):
    """Wrap ``fn`` so a worker returns ``(result, error)`` instead of raising.

    ``ThreadPoolExecutor.map`` re-raises the first worker exception at the point
    of iteration and abandons the remaining results. Documentation preparation
    reports per-candidate failures and must keep the candidates that did
    succeed, so failures travel back as values.
    """

    def settled(item):
        try:
            return fn(item), None
        except Exception as exc:  # reported per candidate by the caller
            return None, exc

    return settled


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
    "BRIGHTDATA_",
    "DAYTONA_",
    "DEEPSEEK_",
    "DOUBLEWORD_",
    "KIMI_",
    "MINIMAX_",
    "MOONSHOT_",
    "OPENAI_",
    "OPENROUTER_",
    "ORCHESTRATOR_",
    "OXYLABS_",
    "SCRAPEDO_",
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
2a. Treat the systems they ALREADY run as a hard selection signal, not background
   colour. Whatever platform they name, that vendor's own first-party option for this
   category is a leading candidate, because it inherits their identity, permissions, connectors
   and billing. A shortlist that omits the same-vendor answer is a worse shortlist even
   when the tools on it are individually stronger. Identify that option by searching for
   it, never by assuming which vendor it is.
2b. Rank candidates on the criteria the user actually stated: fit with the stack they
   already run, total cost at the scale they described, and the specific constraints
   they named. Popularity and market share are not criteria. If a constraint they gave
   rules a tool out, say so plainly rather than including it to pad the list.
2c. Cover both shapes of solution, not one: managed services someone can buy, AND
   libraries someone must assemble. A list of only libraries answers a different
   question from the one a team with an existing estate is asking.
2d. ALWAYS shortlist 1-3 documented building blocks a team would compose to build the
   thing themselves — each as its own candidate with role "build_component", a real
   docs_url, and the correct kind. Every tool_assessment spec must contain at least one.
   Unconditional, on purpose. Do NOT make this depend on the user stating a must-have,
   on your having recorded one in constraints, or on the products looking weak. At this
   stage you cannot know whether any product satisfies the requirement — that is decided
   later, by reading each product's documentation and scoring it. If you shortlist
   products only and every one of them then fails, the run ends with no answer at all
   and no way to add one: the user is told what does not work and never told what would.
   This supplements the field of marketed products; it never replaces searching for
   them, so keep the products you found on the shortlist too. You do not decide that
   building is the better answer. Every candidate, product and component alike, is
   scored from its own documentation, and the report's verdict concludes
   self-implementation only when the measured scores support it.
3. If they named specific tools, find each official implementation guide. If not,
   discover candidates with the dialect sweep in 3a, then scrape_docs on the most
   promising official documentation pages. Prefer primary vendor docs over reviews.
   Search for the user's own platform by name alongside the generic category, or the
   results will be listicles of whatever is popular rather than what fits their stack.
3a. Search in more than one dialect, because a niche tool is invisible in the wrong
   one. Repeated runs kept surfacing the same handful of popular tools while a
   known-good product never appeared at all: it describes itself in its AUDIENCE'S
   dialect, not the searcher's, and one query dialect draws from one pool forever.
   So before searching, silently list the DIFFERENT NAMES this capability goes by for
   different audiences — 3 to 5 of them: the buyer, the practitioner who uses it daily,
   the end consumer in app-store language ("<job> app"), and the developer in
   library/API language. What a buyer calls a question generation tool is a worksheet
   generator to a teacher, an assessment authoring platform to procurement, a homework
   solver or practice app in a store listing, and a question bank API to a developer —
   the same capability, four pools of results. Then run ONE web_search per dialect,
   phrasing each query in that audience's own words, not the user's. Two additional
   query shapes when they fit: the community question a practitioner would actually ask
   ("what do <practitioners> use to <job>"), and "<dominant well-known tool>
   alternatives". The user's own phrasing is ONE dialect among several, never the only
   one. Among the sweep's queries, at least one must use the SUPERORDINATE CATEGORY
   term rather than the user's subject term — one rung up the category ladder, as
   subject-level "math practice" is to category-level "STEM learning" — because a
   product routinely positions itself at the broader category and is then invisible to
   every subject-level query in every dialect. And when a plausible audience is an
   individual end user — a student, a consumer, a professional on their own device —
   one query must be CHANNEL-SHAPED for app-store distribution ("<job> app iphone",
   "<job> app android"), because store-first products rank as store listings rather
   than as web tools and are otherwise invisible to tool-shaped web queries.
4. When you have enough, propose the benchmark spec as a fenced ```json block
   with EXACTLY this shape:
   {"benchmark_type": "tool_assessment",
    "category": str,
    "objective": str,
    "constraints": {"stack": [str], "must_have": [str], "budget": str, "deployment": str},
    "candidates": [{"name": slug, "display_name": str, "docs_url": str,
                    "pricing_url": str, "kind": "local_tool"|"hosted_api"|"saas",
                    "role": "product"|"build_component"}],
    "excluded": [{"name": slug, "display_name": str,
                  "kind": "violation"|"not_assessed", "violates": str}]}
   role defaults to "product" and is omitted for anything a team would buy or adopt
   whole. Use "build_component" only for a piece they would compose themselves under 2d.
   excluded is optional and defaults to empty. It is the honest record of the cut:
   list there the candidates the sweep surfaced and you considered but did not
   shortlist, each with kind "not_assessed" — they were never measured and no
   requirement strike is claimed against them, so their violates line is supplied for
   you and you may omit it. kind "violation" is for a candidate a stated constraint
   rules out, and then violates must name that constraint.
    constraints records only what the user actually stated, in their own terms:
    platforms they already run in "stack", hard requirements in "must_have", spend or
    scale in "budget", hosting or residency requirements in "deployment". Omit
    anything they did not state. Never invent a constraint: this object is the
    audit record of what the shortlist was selected against.
   A self-hosted runner, agent, worker, gateway, or connector is a feature of a
   candidate, so record that phrase in "must_have". It does NOT mean the product,
   platform, service, or solution itself must be self-hosted. Set "deployment" to
   self-hosted or on-premises only when the user explicitly applies that requirement
   to the overall product, platform, service, solution, system, tool, or deployment.
5. Every candidate must have a real docs_url. A URL returned by web_search is enough on
   its own — scrape_docs only enriches it, and is never required to propose the spec.
   pricing_url is the public pricing page's URL; when a candidate has no such page —
   open source, contact-sales-only, free, whatever the reason — declare that with "",
   never with prose. Do not use the built-in OCR candidates unless the user explicitly
   asks for OCR.
5a. Scraping fails often: vendors block automated fetches, and that is expected, not a
   problem to solve. If scrape_docs returns an error, DO NOT retry it, do not try a
   different page for the same candidate, and do not search again for a replacement —
   keep the docs_url you already have and move on. Two failed scrapes in a turn means
   stop scraping entirely and propose the spec with the URLs you have.
5b. The sweep in 3a is the entire search budget for a turn: at most 6 web_search calls,
   each one a DIFFERENT dialect or query shape. Repeating a near-identical query is the
   one thing that is forbidden — it redraws from the same pool and returns the same
   names you already have. The category-ladder query and the app-store channel query
   from 3a count inside that budget, so the sweep PRIORITISES: the user's phrasing, one
   other audience dialect, the category-ladder query, and the channel query when an
   individual end user is plausible; whatever budget remains goes to more dialects.
   After the sweep, propose the spec. Searching beyond the
   budget does not make the spec better, and leaving the user without one is the worst
   outcome. Shortlist 4-8 candidates, chosen across dialects whenever the sweep
   surfaced credible candidates from more than one: a shortlist drawn from a single
   dialect wastes the sweep. Only products count toward 4-8; the building blocks
   required by 2d are additional.
6. A tool_assessment rates documented implementation feasibility. It does NOT score a
   labelled dataset, so never describe its output as extraction accuracy.
7. Keep replies concise and concrete. Explain that implementation is attempted only when
   the docs are sufficient; otherwise execution is skipped and the tool receives a rating.
   Never name the sandbox or scraping services by vendor; describe what happened, not who did it."""

# Both benchmark kinds are always on the table. Which one a question deserves is
# a property of the QUESTION, never of what the user happened to attach: some
# questions are settled by measuring candidates on labelled examples, and some
# are settled by comparing what the candidates are. Intake therefore always sees
# this block, and %(dataset_clause)s tells it whether the labelled examples
# already exist or will be built for the spec it writes.
MEASURED_INTAKE_TEMPLATE = INTAKE_SYSTEM + """

TWO KINDS OF BENCHMARK. Choose on the nature of the question, never on whether data
happens to be attached.

Propose a MEASURED benchmark when answering the question requires observing what the
candidates actually DO to the same inputs — when the answer is a number that only
running them can produce (how accurately, how often, how fast, how consistently). Emit
it as a fenced ```json block with EXACTLY this shape:
   {"benchmark_type": "extraction",
    "category": str,
    "fields": %(fields_json)s,
    "candidates": [{"name": slug, "docs_url": str, "pricing_url": str,
                    "kind": "local_tool"|"hosted_api", "use_fallback": bool}]}
%(dataset_clause)s
A measured benchmark runs every candidate over every labelled example in an isolated
sandbox and scores the output against ground truth with a deterministic evaluator, so
prefer it whenever running the candidates would settle the question better than reading
about them. The task is whatever the user's is — extracting fields from documents is one
such task and carries no special status; classification, transcription, parsing,
translation, tagging, structuring, and any other job with a checkable right answer are
equally measured benchmarks.

Propose a TOOL_ASSESSMENT (the shape given above) when the question is about what the
candidates ARE rather than how well they perform: which ones exist, what they cost, what
they integrate with, whether the documentation supports the integration the user needs,
which to shortlist. A comparison of tools is a complete, first-class answer — it does not
need labelled data and must never be described or apologised for as a lesser one.

ProofBench ships first-party adapters for these candidate names: tesseract, easyocr,
paddleocr, doubleword, openai_vision, nosana_vlm. They are OCR tools and are relevant
only to an OCR question. Set use_fallback=true to use ProofBench's own adapter for one of
those names; the server supplies its credentials. For every other candidate set
use_fallback false and supply real documentation so an adapter can be generated."""

# What the measured block says about where its labelled examples come from. The
# bound form pins the schema to the columns that already exist, because a spec
# whose fields disagree with the ground truth is unscoreable. The unbound form
# lets intake declare the schema the question needs, and ProofBench builds
# examples to match it — so a measured benchmark is never blocked on the user
# having attached something first.
DATASET_CLAUSE_BOUND = """fields MUST be exactly the labelled columns shown above — every {"name", "type"} pair,
in that order, because those columns already have ground truth. The evaluator scores
those and no others."""

DATASET_CLAUSE_UNBOUND = """fields is the schema the question needs: 2 to 10 {"name", "type"} pairs you choose,
each a value that would appear on the documents and has one correct answer. No labelled
examples are attached, so ProofBench builds them to match the fields you declare — you do
not need to ask the user for data, and you must not withhold a measured benchmark for
want of it. Declare fields only for what the question is really asking; every column you
name becomes a column the candidates are scored on."""


# One toolless completion, run once per proposed tool_assessment spec, that
# checks the drafted shortlist against the constraints the user actually stated.
# It is an advisory gate: any failure — provider error, malformed JSON, a verdict
# that would empty the field — leaves the spec exactly as drafted. Its value is
# the audit record: a dropped candidate is persisted with the constraint it
# violated, so the report can answer "why isn't X on this list" with evidence.
SHORTLIST_REVIEW_SYSTEM = """You review a benchmark shortlist against the constraints the
user stated. Drop a candidate ONLY when a stated constraint or the stated objective clearly
rules it out; when unsure, keep it. Popularity is never a reason to drop or keep.
A candidate whose role is "build_component" is a building block for a self-built
integration, so judge only whether a stated constraint rules that component itself out:
being a component rather than a finished product is never a violation.
Return strict JSON only, with exactly this shape and nothing else:
{"drop": [{"name": "<candidate slug>", "violates": "<the stated constraint it breaks, one sentence>"}]}
An empty drop list is a good answer. Never drop every candidate."""

BUILD_PATH_SYSTEM = """You name the documented building blocks a team would compose to
build this capability themselves, when no off-the-shelf product will do it for them.
These supplement a shortlist of marketed products that has already been drafted; you are
not judging those products and not deciding that building is better. Something has to be
here, because a run whose products all fail leaves the user with no path at all.
Name 1-3 real, individually documented components: an open-source library, an SDK, a
protocol, or a self-hostable server a developer assembles into their own build. NEVER a
hosted or commercial service — if it has a pricing page and an account signup, it is a
product, not a component, and it does not belong here. Never name something that would
itself belong in the product field being compared: a rival of the shortlisted products is
a product however useful it would be. Each needs an official documentation URL you are
confident exists. Prefer well-established components over obscure ones. Never invent a
URL, and never repeat a product already on the shortlist.
Return strict JSON only, with exactly this shape and nothing else:
{"components": [{"name": "<lowercase_slug>", "display_name": "<real name>",
"docs_url": "<official docs URL>", "kind": "local_tool"}]}
An empty components list is acceptable only when the capability genuinely cannot be
assembled from documented parts."""

# Rule 3a of the intake prompt asks for two query shapes the model honours only
# sometimes. On a live run it ran the category-ladder query, silently skipped the
# app-store channel query, and dropped the objective's distinguishing requirement
# term from most of the sweep. That exact combination — channel shape, one rung up
# the ladder, requirement terms kept — is the only dialect that surfaced a
# store-first consumer product at position 1. So the two queries are written here
# and executed in code rather than asked for again.
DISCOVERY_REACH_SYSTEM = """You write two search queries that reach pools of results a
tool-shaped web search never returns. You are given an objective, the constraints the user
stated, and the candidates already shortlisted.
ladder_query restates the same job ONE RUNG UP the category ladder — the broader category
a product positions itself in rather than the subject the user named, as subject-level
"math practice" is to category-level "STEM learning" — and it MUST keep the objective's
distinguishing requirement terms, the words that separate what the user wants from the
generic version of the job. Dropping those terms is the failure this exists to prevent.
channel_query is app-store shaped: "<job> app iphone", in the words an individual end user
would type on their own device, and it MUST keep the same distinguishing requirement terms.
Phrase the channel query's job at the LADDER level too, and in the end user's ACTIVITY
words — the word for what the person is DOING at the moment of need, never the industry's
institutional noun for it. A student is doing "homework" or "studying", not "learning"; a
clinician is "charting", not "clinical documentation". Store-first products title
themselves in those activity words, and the institutional noun misses them entirely —
measured: the activity word surfaced a store-first product at result 1 that the
institutional synonym of the same query never returned. When AI products plausibly exist
for this job, include the token "AI" in the channel query; store titles lead with it.
Never name a specific product in either query — a query built around one name returns that
name and its imitators. Keep each query under 200 characters.
Return strict JSON only, with exactly this shape and nothing else:
{"ladder_query": "<query>", "channel_query": "<query>"}"""

# The harvest half of the same gate. It reads only what the searches actually
# returned, and every URL it may cite is checked against that list afterwards,
# because a docs_url the model composed from memory is the one thing this pass
# could quietly add that nothing downstream would catch.
DISCOVERY_HARVEST_SYSTEM = """You pick candidates out of raw search results that the
drafted shortlist missed. You are given an objective, the names already shortlisted or
already excluded, and a list of search results as title and URL pairs.
Name at most 3 candidates that plausibly do the objective's job and are not already listed.
Do NOT require a result's title to restate the objective's words. Rows marked source
"channel" are app-store listings: store titles speak the end user's activity words, not the
buyer's tool-category words, so the listing whose title least resembles the objective may be
exactly the job — that mismatch is why these rows were fetched at all. When a channel row
plausibly does the job, prefer including at least one such row over a third lookalike of
the candidates already shortlisted; near-duplicates of existing candidates add nothing.
Each docs_url MUST be copied exactly from the supplied results — one of those URLs,
character for character. Never write a URL that is not in the list, never repair or shorten
one, and never name a candidate you cannot point at a supplied URL for. Returning fewer
candidates, or none, is a good answer; inventing one is not.
kind is "local_tool" for something installed and run locally, "hosted_api" for a documented
API a developer calls, "saas" for an application a person signs up for.
Return strict JSON only, with exactly this shape and nothing else:
{"candidates": [{"name": "<lowercase_slug>", "display_name": "<real name>",
"docs_url": "<a URL copied from the results>", "kind": "local_tool"|"hosted_api"|"saas"}]}"""

# One toolless completion, run once at the start of a session, that restates the
# request as a research brief. It amplifies; it never extends. A brief that
# invented a requirement would send the whole turn researching something the user
# never asked for — and because the brief is prepended to the system prompt, that
# invention would read to the model as the user's own words. Hence the hard rule
# below: what is unknown is named as unknown, never filled in.
PROMPT_BRIEF_SYSTEM = """You restate a benchmarking request as a short research brief for
another agent. You are amplifying the request, never replacing or extending it.

Extract the category, objective, and constraints ONLY from what the user stated or clearly
implied. Never invent a requirement, a vendor, a product, a budget, or a preference the
user did not give: a detail you add becomes something the next agent researches instead of
their actual question. If the user was vague, the brief stays vague.

A self-hosted runner, agent, worker, gateway, or connector is a feature requirement and
belongs in "must_have"; it does not make the overall product deployment self-hosted. Use
"deployment" for self-hosted or on-premises only when the user explicitly applies it to
the product, platform, service, solution, system, tool, or deployment as a whole.

Between what they stated and what they left open sits what they clearly meant. In
"inferred_context", infer the context their wording actually supports: who will use this,
what environment it runs in, what scale it operates at, what deliverable they are working
towards. Each inference must carry its "basis": the words in their message that imply it.
An inference you cannot ground in something they wrote is not an inference — it is an
unknown, and belongs there instead.

Aspects of the decision the user has not addressed, and which nothing in their wording
implies, go in "unknowns", each as a short noun phrase naming the missing aspect itself.
Naming a gap is useful; guessing at it is not.

search_angles are 2-4 concrete ways to search for candidates that fit the stack and
constraints the user actually described, and may draw on inferred context: searching on an
inference explores it, which is how an assumption gets tested rather than assumed. Name no
specific vendors or products in them.

"improved_prompt" is their request rewritten clearly and specifically, in plain prose, at
most 600 characters. Build it from what they stated plus the inferences you declared above
and nothing else. Name no vendor or product they did not name themselves. This is the
wording the search will actually run on, so it must still be recognisably their request.

"complete" is true only when their message already pins down the objective, at least one
hard requirement, and the context it runs in — that is, when asking them to confirm a
direction would add nothing they have not already said. When their request is broad,
underspecified, or open to materially different readings, it is false.

Return strict JSON only, with exactly this shape and nothing else:
{"category": str,
 "objective": str,
 "constraints": {"stack": [str], "must_have": [str], "budget": str, "deployment": str},
 "inferred_context": [{"assumption": str, "basis": str}],
 "unknowns": [str],
 "search_angles": [str],
 "improved_prompt": str,
 "complete": true|false}
Every field must be present. Use empty lists and empty strings where the user said
nothing; an empty field is the correct answer when they gave you nothing to put in it."""

SPEC_RETRY_NUDGE = """That request already names the candidates and states an extraction
objective, and a labelled dataset is bound to this session. Propose the extraction
benchmark spec now as a fenced ```json block in the documented shape, using only the
candidates, fields, and objective already established. Reply with a question instead
only if a required part of the spec is genuinely still unknown."""

# How many turns intake may take before it must answer. Tool calls spend a turn
# each, so this has to leave room for real research: the previous budget of 8 was
# consumed by searching alone on a broad question ("what service for a RAG
# chatbot"), and the user got an apology instead of the eighteen sites' worth of
# findings the agent had already gathered.
INTAKE_ROUNDS = 14

# Failed scrapes tolerated per intake turn before the tool is withdrawn. Vendor
# documentation blocks automated fetches routinely, so a failure says nothing
# about the candidate and retrying it only burns the budget.
MAX_INTAKE_SCRAPE_FAILURES = 2

# Sent on the last round, which is offered no tools. The model cannot search
# again, so it answers with what it has — a recommendation or a genuine question,
# either of which is worth more than "rephrase your request".
INTAKE_WRAP_UP_NUDGE = """You have no further tool calls available. Answer now using
what you already gathered: either propose the benchmark spec as a fenced ```json block
in the documented shape, or, if something required is genuinely still unknown, summarise
your findings so far and ask the single most important question. Do not say you are
going in circles and do not ask the user to rephrase."""

# The exact spec contract handed to the distinct supervisor when the primary
# researched a turn but returned prose instead of a fenced spec. It restates the
# two documented shapes verbatim so the supervisor produces exactly what
# _extract_spec/_normalize_intake_spec will accept — nothing wider.
SPEC_RECOVERY_CONTRACT_TOOL_ASSESSMENT = """Return ONLY a single fenced ```json block, no prose before or after, with EXACTLY this shape:
{"benchmark_type": "tool_assessment",
 "category": str,
 "objective": str,
 "constraints": {"stack": [str], "must_have": [str], "budget": str, "deployment": str},
 "candidates": [{"name": slug, "display_name": str, "docs_url": str,
                 "pricing_url": str, "kind": "local_tool"|"hosted_api"|"saas",
                 "role": "product"|"build_component"}],
 "excluded": [{"name": slug, "display_name": str,
               "kind": "violation"|"not_assessed", "violates": str}]}
Every candidate needs a real docs_url. pricing_url is a URL or "" when there is no public pricing page — never prose. Include at least one role "build_component". constraints records only what the user actually stated; omit what they did not. excluded is optional. Draw candidates and URLs only from the conversation and gathered findings — invent nothing."""

SPEC_RECOVERY_CONTRACT_EXTRACTION_TEMPLATE = """Return ONLY a single fenced ```json block, no prose before or after, with EXACTLY this shape:
{"benchmark_type": "extraction",
 "category": str,
 "fields": %(fields_json)s,
 "candidates": [{"name": slug, "docs_url": str, "pricing_url": str,
                 "kind": "local_tool"|"hosted_api", "use_fallback": bool}]}
fields must be exactly the dataset's labelled columns as listed. Draw candidates and URLs
only from the conversation and gathered findings — invent nothing."""


def _default_fields_json() -> str:
    from engine.fields import DEFAULT_FIELDS

    return json.dumps([{"name": f.name, "type": f.type} for f in DEFAULT_FIELDS])


def spec_recovery_contract_extraction(dataset_fields: list | None = None) -> str:
    fields_json = (json.dumps(dataset_fields) if dataset_fields
                   else _default_fields_json())
    return SPEC_RECOVERY_CONTRACT_EXTRACTION_TEMPLATE % {"fields_json": fields_json}

# Shown when a researched turn owed the user a spec, the primary returned prose,
# and no distinct supervisor could recover one. It is honest and actionable — it
# never presents the shortlist prose as if the benchmark were finished.
SPEC_RECOVERY_UNAVAILABLE = (
    "I researched candidates for this but did not produce a runnable benchmark spec, "
    "and no independent supervisor model is configured to correct that (set "
    "SUPERVISOR_PROVIDER or SUPERVISOR_MODEL to a model distinct from the orchestrator). "
    "Nothing was benchmarked. Tell me which of the candidates above to compare — or "
    "confirm the direction — and I will propose the spec."
)

# Extraction wording in the user's own words; never used to fill the spec.
_EXTRACTION_INTENT = re.compile(
    r"extract|ocr|invoice|receipt|scanned|document|field", re.IGNORECASE
)


def _compact(text: str) -> str:
    """Lowercase alphanumerics only, so "OpenAI Vision" matches openai_vision."""
    return re.sub(r"[^a-z0-9]+", "", str(text or "").casefold())


_INTAKE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


_PROVIDER_MESSAGE_RE = re.compile(r"""['"]message['"]\s*:\s*['"](.+?)['"]\s*[,}]""", re.S)


def _readable_error(text: object, limit: int = 220) -> str:
    """Reduce a provider exception to the one sentence a buyer can act on.

    An SDK error arrives as ``RateLimitError: Error code: 429 - {'error':
    {'message': 'You exceeded your current quota...', 'type': ...}}``. Shown
    raw it truncates mid-payload and reads as noise, so the embedded message
    replaces the dict and the rest is dropped.
    """
    value = " ".join(str(text or "").split())
    brace = value.find("{")
    if brace >= 0:
        match = _PROVIDER_MESSAGE_RE.search(value[brace:])
        head = value[:brace].rstrip(" -").rstrip()
        value = f"{head} - {match.group(1)}" if match and head else (
            match.group(1) if match else head or value)
    if len(value) > limit:
        cut = value.rfind(" ", 0, limit)
        value = value[: cut if cut > limit // 2 else limit].rstrip(" ,.;:") + "..."
    return value


def _adapter_error_summary(diagnostic_lines: list[str]) -> str:
    """Pull the actionable reason out of an adapter's validation output.

    The sandbox prints a ``RESULT_JSON:`` line carrying the exception that
    stopped the adapter. That string is what a buyer needs ("quota exceeded",
    "401 unauthorized"), so it is preferred over the surrounding noise.
    """
    for line in reversed(diagnostic_lines or []):
        marker = line.find("RESULT_JSON:")
        if marker < 0:
            continue
        try:
            payload = json.loads(line[marker + len("RESULT_JSON:"):])
        except (ValueError, TypeError):
            continue
        if isinstance(payload, dict) and payload.get("error"):
            return _readable_error(payload["error"])
    for line in reversed(diagnostic_lines or []):
        if line.strip():
            return _readable_error(line)
    return "the adapter produced no output"


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


def _findings_from_result(name: str, args: dict, result: object) -> list[dict]:
    """Titles and URLs worth carrying into the next turn.

    Only what identifies a candidate — never page bodies. web_search returns the
    list this is really for; a successful scrape contributes the one URL it read,
    so a page already fetched is not fetched again.
    """
    try:
        parsed = json.loads(result if isinstance(result, str) else "null")
    except (json.JSONDecodeError, TypeError):
        return []

    found: list[dict] = []
    if name == "web_search":
        rows = parsed if isinstance(parsed, list) else (parsed or {}).get("results")
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            url = str(row.get("url") or "").strip()
            if url:
                found.append({"title": str(row.get("title") or url).strip(), "url": url})
    elif name == "scrape_docs":
        url = str((args or {}).get("url") or "").strip()
        if url:
            found.append({"title": url, "url": url})
    return found[:_MAX_FINDINGS_PER_CALL]


_MAX_FINDINGS_PER_CALL = 12

# How many prior findings to replay. Enough to stand in for a session's research,
# small enough that the prompt stays about the user's question.
_MAX_FINDINGS_IN_PROMPT = 40


def _findings_digest(findings: list[dict] | None) -> str:
    """Prior research as prompt text, or "" when there is none."""
    lines = []
    seen: set[str] = set()
    for item in findings or []:
        url = str((item or {}).get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        title = str((item or {}).get("title") or url).strip()
        lines.append(f"- {title} — {url}")
        if len(lines) >= _MAX_FINDINGS_IN_PROMPT:
            break
    if not lines:
        return ""
    return (
        "Already researched earlier in this conversation. Reuse these instead of "
        "searching for them again; search only for what is genuinely missing:\n"
        + "\n".join(lines)
    )


def _tool_result_failed(result: object) -> bool:
    """True when dispatch_tool reported an error rather than content.

    dispatch_tool never raises; it returns a JSON string carrying an ``error``
    key instead, so a failure is only visible by reading the payload back.
    """
    try:
        parsed = json.loads(result if isinstance(result, str) else "{}")
    except (json.JSONDecodeError, TypeError):
        return False
    return isinstance(parsed, dict) and bool(parsed.get("error"))


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


def _parse_build_components(content: str, taken_names: set[str]) -> list[dict]:
    """Extract proposed build components, keeping only usable ones.

    Model-authored, so nothing here is trusted: a component needs a valid slug
    the shortlist does not already use and a public HTTP(S) docs URL, or it is
    discarded rather than repaired. Capped at three — this supplements a field
    of products, it does not become the field.
    """
    text = str(content or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    value = json.loads(text)
    if not isinstance(value, dict) or not isinstance(value.get("components"), list):
        raise ValueError("build path reply must be an object with a components list")
    components: list[dict] = []
    seen = set(taken_names)
    for raw in value["components"][:3]:
        if not isinstance(raw, dict):
            continue
        display_name = str(raw.get("display_name") or raw.get("name") or "").strip()[:160]
        name = _intake_slug(raw.get("name") or display_name)
        docs_url = str(raw.get("docs_url") or "").strip()[:2048]
        if not _INTAKE_NAME_RE.fullmatch(name) or name in seen:
            continue
        if not _valid_intake_url(docs_url):
            continue
        # This gate only ever adds library-shaped parts, so a hosted service
        # arriving here is a rival product coming in through the component door:
        # asked to compare Resend and Postmark, the pass returned SendGrid and
        # Mailgun alongside Nodemailer, and one of them scored above the actual
        # winner. Anything not local_tool is dropped rather than repaired; an
        # unlabelled kind is simply what the prompt asked for. Components written
        # by intake are normalized elsewhere and keep their freedom.
        kind = str(raw.get("kind") or "").strip().casefold()
        if kind in {"hosted_api", "saas"}:
            continue
        kind = "local_tool"
        seen.add(name)
        components.append({
            "name": name, "display_name": display_name or name, "docs_url": docs_url,
            "pricing_url": "", "kind": kind, "role": "build_component",
        })
    return components


def _parse_discovery_queries(content: str) -> tuple[str, str]:
    """The two supplementary queries, either of which may be "".

    Model-authored, so neither string is repaired: a missing, empty, or
    non-string query is dropped rather than patched. Each query is judged on
    its own — one malformed field must not cost the pool the other query still
    reaches — and the raise the caller fails open on is reserved for a reply
    with nothing usable in it at all. Bounded at 200 characters because a query
    long enough to matter is already wrong.
    """
    value = json.loads(_unfenced_json_text(content))
    if not isinstance(value, dict):
        raise ValueError("discovery reach reply must be an object")
    queries = []
    for key in ("ladder_query", "channel_query"):
        raw = value.get(key)
        if not isinstance(raw, str) or not raw.strip():
            queries.append("")
            continue
        queries.append(" ".join(raw.split())[:200])
    if not any(queries):
        raise ValueError("discovery reach reply carries no usable query")
    return queries[0], queries[1]


def _parse_discovery_candidates(
    content: str, taken_names: set[str], allowed_urls: set[str]
) -> list[dict]:
    """Extract candidates harvested from supplementary results.

    Nothing is trusted. A candidate needs a valid slug no existing candidate or
    exclusion already uses, and a docs_url that is literally one of the URLs the
    searches returned — a plausible-looking URL the model wrote from memory is
    exactly what this check exists to reject. Capped at three: this gate widens
    the field, it does not replace it.
    """
    value = json.loads(_unfenced_json_text(content))
    if not isinstance(value, dict) or not isinstance(value.get("candidates"), list):
        raise ValueError("discovery reach harvest must be an object with a candidates list")
    harvested: list[dict] = []
    seen = set(taken_names)
    for raw in value["candidates"][:3]:
        if not isinstance(raw, dict):
            continue
        display_name = str(raw.get("display_name") or raw.get("name") or "").strip()[:160]
        name = _intake_slug(raw.get("name") or display_name)
        docs_url = str(raw.get("docs_url") or "").strip()
        if not _INTAKE_NAME_RE.fullmatch(name) or name in seen:
            continue
        if docs_url not in allowed_urls or not _valid_intake_url(docs_url):
            continue
        kind = str(raw.get("kind") or "").strip().casefold()
        if kind not in {"local_tool", "hosted_api", "saas"}:
            kind = "saas"
        seen.add(name)
        harvested.append({
            "name": name, "display_name": display_name or name, "docs_url": docs_url,
            "pricing_url": "", "kind": kind, "role": "product",
        })
    return harvested


def _unfenced_json_text(content: str) -> str:
    """A model reply's JSON body, with a ```json fence stripped if it wrote one.

    Both toolless side calls (shortlist review, prompt brief) ask for bare JSON
    and get a fenced block often enough that refusing one would fail a reply that
    is otherwise perfectly good.
    """
    text = str(content or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _parse_shortlist_verdict(content: str, valid_names: set[str]) -> list[dict]:
    """Extract the drop list from a review reply, keeping only real candidates.

    The verdict is model-authored, so nothing in it is trusted: unknown names,
    duplicate names, and entries with no named constraint are discarded rather
    than repaired. A verdict that cannot be parsed at all raises, and the caller
    fails open to the spec as drafted.
    """
    value = json.loads(_unfenced_json_text(content))
    if not isinstance(value, dict) or not isinstance(value.get("drop"), list):
        raise ValueError("shortlist verdict must be an object with a drop list")
    drops: list[dict] = []
    seen: set[str] = set()
    for raw in value["drop"][:20]:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        violates = str(raw.get("violates") or "").strip()[:300]
        if name in valid_names and name not in seen and violates:
            seen.add(name)
            drops.append({"name": name, "violates": violates})
    return drops


def _intake_slug(value: object) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip(".-")
    return slug[:64]


def _spec_display_name(spec: object, name: str) -> str:
    """The name a person would recognise for a candidate, or its slug.

    Metrics are keyed by slug, so without this every report and every console
    row printed `azure_ai_search_openai` where the spec said "Azure AI Search +
    Azure OpenAI". Falls back to the slug rather than raising: a run whose spec
    is missing still has to produce a readable report.
    """
    for candidate in ((spec or {}).get("candidates") or []) if isinstance(spec, dict) else []:
        if isinstance(candidate, dict) and str(candidate.get("name") or "") == name:
            return str(candidate.get("display_name") or name)
    return name


def _spec_role(spec: object, name: str) -> str:
    """Whether this row is a marketed product or a self-build component.

    Travels with the row for the same reason display_name does: the report and
    the verdict have only the metrics dict to read, and a build path cannot be
    reconstructed from a slug. Anything unrecognised is a product, so the build
    path is only ever claimed by a spec that said so.
    """
    for candidate in ((spec or {}).get("candidates") or []) if isinstance(spec, dict) else []:
        if isinstance(candidate, dict) and str(candidate.get("name") or "") == name:
            role = str(candidate.get("role") or "").strip().casefold()
            return role if role in {"product", "build_component"} else "product"
    return "product"


# The constraint object is the audit record of what the shortlist was selected
# against, so it is bounded the same way every other intake field is: nothing
# model-authored reaches the server unclamped.
_CONSTRAINT_LIST_KEYS = ("stack", "must_have")
_CONSTRAINT_TEXT_KEYS = ("budget", "deployment")
_SELF_HOSTED_COMPONENT_RE = re.compile(
    r"\bself[- ]hosted\s+(?:(?:ci|cd|ci/cd)\s+)?"
    r"(?:runner|agent|worker|gateway|connector)s?\b",
    re.IGNORECASE,
)
_OVERALL_SELF_HOSTED_RE = re.compile(
    r"(?:"
    r"\bself[- ]hosted\s+(?:product|platform|service|solution|system|tool|deployment)\b"
    r"|"
    r"\b(?:product|platform|service|solution|system|tool|deployment)\b"
    r"(?:\s+itself)?\s+(?:must|needs?\s+to|has\s+to|should)\s+"
    r"(?:be\s+|run\s+|deploy(?:ed)?\s+)?"
    r"(?:self[- ]hosted|on[- ]prem(?:ises)?)\b"
    r"|"
    r"\b(?:must|needs?\s+to|has\s+to|should)\s+"
    r"(?:be\s+|run\s+|deploy(?:ed)?\s+)"
    r"(?:self[- ]hosted|on[- ]prem(?:ises)?)\b"
    r")",
    re.IGNORECASE,
)


def _normalize_intake_constraints(raw: object, request_text: str = "") -> dict:
    """Bound the user's stated constraints; absent stays absent, never invented."""
    if not isinstance(raw, dict):
        return {}
    normalized: dict = {}
    for key in _CONSTRAINT_LIST_KEYS:
        items = raw.get(key)
        if isinstance(items, list):
            values = []
            for item in items[:12]:
                value = str(item or "").strip()[:120]
                if value and value not in values:
                    values.append(value)
            if values:
                normalized[key] = values
    for key in _CONSTRAINT_TEXT_KEYS:
        value = str(raw.get(key) or "").strip()[:300]
        if value:
            normalized[key] = value
    component = _SELF_HOSTED_COMPONENT_RE.search(request_text or "")
    deployment = str(normalized.get("deployment") or "")
    if (component and re.search(r"\b(?:self[- ]hosted|on[- ]prem(?:ises)?)\b",
                                deployment, re.IGNORECASE)
            and not _OVERALL_SELF_HOSTED_RE.search(request_text or "")):
        normalized.pop("deployment", None)
        must_have = normalized.setdefault("must_have", [])
        feature = component.group(0).strip()
        if feature and feature.casefold() not in {item.casefold() for item in must_have}:
            must_have.append(feature[:120])
    return normalized


_BRIEF_KEYS = ("category", "objective", "constraints", "inferred_context",
               "unknowns", "search_angles", "improved_prompt", "complete")


def _build_prompt_brief(content: str, request_text: str = "") -> dict:
    """Validate and bound one model-authored research brief, or raise.

    The brief is prepended to the intake system prompt, so anything it says
    reads to the next model as though the user had said it. That makes a
    half-parsed brief worse than none at all: a malformed reply raises with the
    reason, so the one repair round can feed back what was actually wrong.
    """
    try:
        value = json.loads(_unfenced_json_text(content))
    except (TypeError, ValueError):
        raise ValueError("the reply is not valid JSON")
    if not isinstance(value, dict) or any(key not in value for key in _BRIEF_KEYS):
        raise ValueError(
            "the reply must be a JSON object carrying every documented key: "
            + ", ".join(_BRIEF_KEYS))
    category = str(value.get("category") or "").strip()[:128]
    objective = str(value.get("objective") or "").strip()[:1000]
    if not category and not objective:
        # A brief that names neither restates nothing; there is no amplification
        # to be had from it, and rendering it would just add noise to the prompt.
        raise ValueError("the brief names neither a category nor an objective")
    unknowns = [
        str(item or "").strip()[:80]
        for item in (value.get("unknowns") or [])[:6]
        if isinstance(value.get("unknowns"), list) and str(item or "").strip()
    ]
    angles = [
        str(item or "").strip()[:160]
        for item in (value.get("search_angles") or [])[:4]
        if isinstance(value.get("search_angles"), list) and str(item or "").strip()
    ]
    # An inference without its basis is indistinguishable from an invention, and
    # the user cannot correct what they cannot see the reasoning for. Entries
    # missing either half are dropped rather than repaired.
    inferred: list[dict] = []
    raw_inferred = value.get("inferred_context")
    for item in (raw_inferred if isinstance(raw_inferred, list) else [])[:5]:
        if not isinstance(item, dict):
            continue
        assumption = str(item.get("assumption") or "").strip()[:160]
        basis = str(item.get("basis") or "").strip()[:160]
        if assumption and basis:
            inferred.append({"assumption": assumption, "basis": basis})
    return {
        "category": category,
        "objective": objective,
        "constraints": _normalize_intake_constraints(value.get("constraints"), request_text),
        "inferred_context": inferred,
        "unknowns": unknowns,
        "search_angles": angles,
        # The wording the search will run on, so it is bounded like everything
        # else here. "complete" is only ever a real boolean: a truthy string
        # would silently skip the confirmation the user is owed.
        "improved_prompt": str(value.get("improved_prompt") or "").strip()[:600],
        "complete": value.get("complete") is True,
    }


# The line every not_assessed exclusion carries. Fixed here, never model-authored:
# nothing was measured about these candidates, so the record must not read as a
# finding against them.
NOT_ASSESSED_NOTE = "Surfaced in discovery; not shortlisted — no requirement strike recorded."


def _normalize_intake_excluded(raw: object, shortlisted: set[str]) -> list[dict]:
    """Bound the model-authored record of what left the field.

    Two kinds travel here. A "violation" is a candidate a stated constraint rules
    out, and it must name the constraint. A "not_assessed" is one discovery
    surfaced and intake considered but did not shortlist: nothing was measured
    and no requirement strike is claimed, so its line is written here rather than
    by the model, which cannot be allowed to invent a strike it never checked.
    """
    if not isinstance(raw, list):
        return []
    normalized: list[dict] = []
    seen: set[str] = set()
    for item in raw[:20]:
        if not isinstance(item, dict):
            continue
        display_name = str(item.get("display_name") or item.get("name") or "").strip()[:160]
        name = _intake_slug(item.get("name") or display_name)
        if not _INTAKE_NAME_RE.fullmatch(name) or name in seen or name in shortlisted:
            continue
        kind = str(item.get("kind") or "").strip().casefold()
        if kind != "not_assessed":
            kind = "violation"
        if kind == "not_assessed":
            violates = NOT_ASSESSED_NOTE
        else:
            violates = str(item.get("violates") or "").strip()[:300]
            if not violates:
                continue
        seen.add(name)
        normalized.append({"name": name, "display_name": display_name or name,
                           "kind": kind, "violates": violates})
    return normalized


def _normalize_intake_spec(
    spec: object, dataset_available: bool, request_text: str = ""
) -> dict | None:
    """Return only a spec the strict run schema can accept, or no spec at all."""
    if not isinstance(spec, dict) or not isinstance(spec.get("candidates"), list):
        return None
    declared = str(spec.get("benchmark_type") or "").strip()
    if declared not in {"extraction", "tool_assessment"}:
        declared = "extraction" if spec.get("fields") else "tool_assessment"
    # A measured benchmark is NOT downgraded for want of an attached dataset.
    # Silently rewriting it to an assessment answered a different question than
    # the one asked and gave no sign it had happened; the spec instead records
    # that its labelled examples are still to be built, and the server builds
    # them from this schema before the run starts.
    generate_dataset = declared == "extraction" and not dataset_available

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
        # The contract asks for a URL or "", but models still write prose here
        # ("Open-source", "contact us", a localized "free"). Whatever the
        # wording, prose in this field is the same declaration of no public
        # pricing page, so URL validity is the whole gate — no keyword list
        # deciding which phrasings count.
        if not _valid_intake_url(pricing_url):
            pricing_url = ""
        if declared == "tool_assessment" and not _valid_intake_url(docs_url):
            continue
        if declared == "extraction" and docs_url and not _valid_intake_url(docs_url):
            docs_url = ""
        kind = str(raw.get("kind") or "").strip().casefold()
        if declared == "tool_assessment":
            if kind not in {"local_tool", "hosted_api", "saas"}:
                kind = "hosted_api" if "api" in kind else "saas"
            # A build component is a piece of a self-built integration rather
            # than something to buy. Anything unrecognised is a product: the
            # build path has to be claimed explicitly, never inferred.
            role = str(raw.get("role") or "").strip().casefold()
            if role not in {"product", "build_component"}:
                role = "product"
            candidate = {"name": name, "display_name": display_name or name,
                         "docs_url": docs_url, "pricing_url": pricing_url, "kind": kind,
                         "role": role}
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
        normalized_spec = {
            "benchmark_type": declared, "category": category, "objective": objective,
            "constraints": _normalize_intake_constraints(spec.get("constraints"), request_text),
            "candidates": normalized_candidates}
        excluded = _normalize_intake_excluded(spec.get("excluded"), seen)
        if excluded:
            normalized_spec["excluded"] = excluded
        return normalized_spec
    fields = []
    for field in spec.get("fields") or []:
        # A field arrives as a bare name or as {name, type}; both normalize to
        # {name, type} so the evaluator's typed comparison travels with the spec.
        if isinstance(field, dict):
            raw_name, raw_type = field.get("name"), field.get("type")
        else:
            raw_name, raw_type = field, None
        normalized = _intake_slug(raw_name)
        if not _INTAKE_NAME_RE.fullmatch(normalized):
            continue
        if normalized in {f["name"] for f in fields}:
            continue
        from engine.fields import FIELD_TYPES, infer_type
        declared_type = str(raw_type) if raw_type in FIELD_TYPES else infer_type(normalized)
        fields.append({"name": normalized, "type": declared_type})
    # The evaluator scores exactly the declared schema; a spec with no scoreable
    # fields cannot be a measured benchmark.
    if not fields or len(fields) > 32:
        return None
    normalized_spec = {"benchmark_type": declared, "category": category, "fields": fields,
                       "candidates": normalized_candidates}
    if generate_dataset:
        normalized_spec["dataset"] = {"source": "generate"}
    return normalized_spec


def intake_system(dataset_available: bool, dataset_fields: list | None = None) -> str:
    """Intake instructions. Both benchmark kinds are always available.

    A bound dataset does not decide which kind of benchmark the user gets — the
    question does. What binding changes is only where the labelled examples come
    from: ``dataset_fields`` pins the spec to columns that already have ground
    truth, and its absence lets intake declare the schema the question needs so
    ProofBench can build examples to match.
    """
    if dataset_available:
        fields_json = (json.dumps(dataset_fields) if dataset_fields
                       else _default_fields_json())
        clause = DATASET_CLAUSE_BOUND
    else:
        fields_json = '[{"name": str, "type": "text"|"date"|"currency"|"number"}, ...]'
        clause = DATASET_CLAUSE_UNBOUND
    return MEASURED_INTAKE_TEMPLATE % {"fields_json": fields_json,
                                       "dataset_clause": clause}

RUN_SYSTEM = """You are ProofBench's orchestrator agent. Execute this protocol strictly,
one phase at a time, using the provided tools. You manage isolated sandboxes;
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


def _orchestrator_complete(env: dict | None = None, *, _producer_sink=None, **kwargs):
    """One orchestration completion, failing over across configured providers.

    Binding the whole conversation to the single most-preferred provider meant a
    provider being rate limited ended the turn outright, even when the
    deployment had another working provider configured.

    ``_producer_sink`` — when a list is passed — receives the ``ModelIdentity``
    that ACTUALLY produced the returned response. After failover that is not the
    configured primary, and independence has to be measured against who really
    produced the artifact, so the caller threads this to supervisor resolution.
    """
    from engine.llm_clients import (
        ModelIdentity,
        capability_providers,
        chat_client,
        provider_model,
    )

    env = os.environ if env is None else env

    def _note(provider: str):
        if _producer_sink is not None:
            _producer_sink.append(ModelIdentity(provider, provider_model(provider, env)))

    # The first attempt goes through the module-level helpers, so a pinned
    # provider and the historical missing-key KeyError both behave as before.
    try:
        response = _orchestrator_client(env).chat.completions.create(
            model=_orchestrator_model(env), **kwargs)
        _note(_orchestrator_provider(env))
        return response
    except Exception as first_error:
        failure = first_error
    for provider in capability_providers("orchestration", env)[1:]:
        try:
            response = chat_client(provider, env).chat.completions.create(
                model=provider_model(provider, env), **kwargs)
            _note(provider)
            return response
        except Exception as exc:
            print(f"[agent] orchestration provider {provider} failed: {type(exc).__name__}",
                  file=sys.stderr)
            failure = exc
    raise failure


class Orchestrator:
    def __init__(
        self,
        run_id: str,
        run_dir: str,
        emit,
        cancel_event=None,
        provider_env=None,
        dataset_available: bool = False,
        dataset_fields: list | None = None,
        run_summary: str = "",
    ):
        self.run_id = run_id
        self.run_dir = run_dir
        self.emit = emit
        self.cancel_event = cancel_event
        # Set by the server when the session has a labelled dataset bound, which
        # is what makes a scored extraction benchmark possible at intake.
        self.dataset_available = bool(dataset_available)
        # candidate name -> resolved snapshot name ("" means none available).
        # Per instance, never class level: a shared dict would leak one
        # deployment's snapshot resolution into another orchestrator.
        self._snapshot_cache: dict[str, str] = {}
        # The bound dataset's labelled columns as {name, type} dicts; intake and
        # spec recovery propose exactly this schema so a spec can never disagree
        # with the ground truth it will be scored against.
        self.dataset_fields = list(dataset_fields or []) or None
        # A factual account of this session's finished run, so a follow-up
        # question is answered from measured numbers rather than guesswork.
        self.run_summary = str(run_summary or "")
        # What this turn discovered, for the server to persist so the next turn
        # inherits it instead of researching the same ground again.
        self.findings: list[dict] = []
        # What earlier turns discovered, injected into the intake prompt.
        self.prior_findings: list[dict] = []
        # Every web_search query this turn dispatched, so the discovery gate can
        # tell which query shapes the model already covered on its own.
        self._turn_search_queries: list[str] = []
        # The (provider, model) identities that ACTUALLY produced this turn's
        # orchestration completions — after failover these are not the configured
        # primary. A supervised review of a model's own output must differ from
        # every one of these, not merely from the configured primary, or a
        # fallback producer could end up reviewing itself. ``_last`` is the
        # producer of the most recent completion (the artifact spec recovery
        # corrects); the set is every producer that fed the drafted shortlist.
        self._orchestration_producers: set = set()
        self._last_orchestration_producer = None
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
        self._last_validation_error: dict[str, str] = {}
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
        self._last_validation_error.clear()
        self.ctx.candidates.clear()
        self.ctx.citations.clear()
        self.ctx.result_keys.clear()
        self.ctx.results_initialized = True
        self.ctx.evaluated_metrics = None
        self.ctx.spec_fields = spec.get("fields") or None
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
                self._discard_research_future()
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

    def _orchestration_complete(self, **kwargs):
        """One orchestration completion, recording who ACTUALLY produced it.

        Wraps the module-level failover helper and threads the real producer
        identity into ``_orchestration_producers`` (every producer this turn) and
        ``_last_orchestration_producer`` (this completion's), so a later
        supervised review is guaranteed distinct from the model that produced the
        artifact even when failover moved off the configured primary. When the
        helper reports nothing — e.g. a test double that ignores the sink — the
        configured primary is assumed, which is exactly the non-failover case.
        """
        from engine.llm_clients import primary_identity

        sink: list = []
        response = _orchestrator_complete(
            self.runtime_env, _producer_sink=sink, **kwargs)
        producer = sink[-1] if sink else primary_identity(
            "orchestration", self.runtime_env)
        if producer is not None:
            self._last_orchestration_producer = producer
            self._orchestration_producers.add(producer)
        return response

    def chat(self, user_message: str) -> None:
        """INTAKE/DISCOVERY conversation; emits deltas and eventually a spec artifact."""
        system_prompt = intake_system(self.dataset_available, self.dataset_fields)
        # Without this the agent answers a question about the run the user is
        # looking at with "I don't have the history of your previous run".
        if self.run_summary:
            system_prompt = f"{system_prompt}\n\n{self.run_summary}"
        # Research carries across turns. Without it each message re-searched from
        # nothing, because only visible text was durable — so a follow-up could
        # lose the candidates the previous turn had already found.
        digest = _findings_digest(self.prior_findings)
        if digest:
            system_prompt = f"{system_prompt}\n\n{digest}"
        # First turn only. A short vague opener is exactly the request that needs
        # its unknowns named, so this does not wait for a long one — but by the
        # second turn the conversation itself is the context, and re-briefing
        # would restate an opening message the model has already read.
        # "First turn" means no prior user message, NOT an empty list: the server
        # seeds _messages with the system prompt (and any restored history)
        # before every call, so an emptiness check made the brief dead code in
        # production while passing on unit-test orchestrators built bare.
        first_turn = not any(m.get("role") == "user" for m in self._messages)
        brief = None
        if first_turn:
            block, brief = self._prepare_brief(user_message)
            if block:
                system_prompt = f"{system_prompt}\n\n{block}"
        if not self._messages:
            self._messages = [{"role": "system", "content": system_prompt}]
        else:
            self._messages[0] = {"role": "system", "content": system_prompt}
        self._messages.append({"role": "user", "content": user_message})

        # A request open to materially different readings is confirmed before it
        # is researched, not after. Searching first and asking later spends the
        # round budget on whichever reading the model picked, and the user only
        # discovers the mismatch once a shortlist arrives. The card is answered
        # by an ordinary chat message, so the confirmation lands in the thread as
        # part of the record rather than as hidden state.
        if first_turn and brief and not brief["complete"] and brief["improved_prompt"]:
            self.emit("artifact", {
                "kind": "direction",
                "improved_prompt": brief["improved_prompt"],
                "assumptions": brief["inferred_context"],
                "unknowns": brief["unknowns"],
            })
            self._delta(
                "Confirm or correct the direction above and I will search on it."
            )
            return
        schemas = [s for s in TOOL_SCHEMAS if s["function"]["name"] in CHAT_TOOLS]
        # At most one internal retry, and only when the request already answers
        # everything the spec needs. Genuine clarification is still returned.
        retry_available = self._extraction_request_is_complete(user_message)
        # Counted per turn, not per session: a site that blocks us today may not
        # tomorrow, and a fresh question deserves a fresh attempt.
        self._scrape_failures = 0
        # Per turn for the same reason: the gate asks what this turn's sweep
        # covered, not what some earlier turn happened to search for.
        self._turn_search_queries = []
        # Per turn as well: independence is measured against the models that
        # produced THIS turn's shortlist and prose, not an earlier turn's.
        self._orchestration_producers = set()
        self._last_orchestration_producer = None

        # Bounded intake loop. Research rounds spend the same budget as reply
        # rounds, so a thorough agent used to exhaust it mid-search and dead-end
        # on "I'm going in circles" — after eighteen useful searches the user got
        # nothing. The budget is larger now, and the FINAL round is offered no
        # tools at all: with nothing left to call the model has to answer, so the
        # loop ends with a real reply instead of an apology.
        for attempt in range(INTAKE_ROUNDS):
            self._check_cancelled()
            final_round = attempt == INTAKE_ROUNDS - 1
            kwargs = {"messages": self._messages}
            if schemas and not final_round:
                kwargs["tools"] = schemas
            if final_round:
                self._messages.append({"role": "user", "content": INTAKE_WRAP_UP_NUDGE})
            resp = self._orchestration_complete(**kwargs)
            msg = resp.choices[0].message

            if msg.tool_calls:
                self._messages.append(msg.model_dump(exclude_none=True))
                for tc in msg.tool_calls:
                    args = json.loads(tc.function.arguments or "{}")
                    name = tc.function.name
                    if name == "web_search":
                        self._turn_search_queries.append(str(args.get("query") or ""))
                    # A blocked vendor page is the norm, not a fixable error. Left
                    # to itself the model reads the failure, picks another page,
                    # fails again, and spends the whole budget doing it — which is
                    # how "going in circles" happened after eighteen calls. Once
                    # scraping has failed twice, the tool is withdrawn: further
                    # calls answer immediately with the standing instruction to
                    # proceed on the URLs already in hand. Guidance alone did not
                    # hold; this makes the loop impossible rather than discouraged.
                    if name == "scrape_docs" and self._scrape_failures >= MAX_INTAKE_SCRAPE_FAILURES:
                        result = json.dumps({
                            "error": "scraping unavailable for this run",
                            "instruction": (
                                "Do not scrape or search again. Propose the benchmark "
                                "spec now using the docs_url values you already have."
                            ),
                        })
                    else:
                        result = dispatch_tool(name, args, self.ctx)
                        if name == "scrape_docs" and _tool_result_failed(result):
                            self._scrape_failures += 1
                        else:
                            self.findings.extend(_findings_from_result(name, args, result))
                    self._messages.append(
                        {"role": "tool", "tool_call_id": tc.id,
                         "content": self._tool_result_for_history(name, args, result)}
                    )
                continue

            text = msg.content or ""
            spec = self._extract_spec(text)
            # Never spend the last round on a retry: there is no round after it
            # to carry the answer, so the reply would be swallowed and the loop
            # would fall through to the give-up line with nothing shown.
            if spec is None and retry_available and not final_round:
                # Ask once more inside the same operation so a complete request
                # does not cost the user a second confirmation turn. The
                # unemitted reply stays in the private message list only.
                retry_available = False
                self._messages.append({"role": "assistant", "content": text})
                self._messages.append({"role": "user", "content": SPEC_RETRY_NUDGE})
                continue
            self._messages.append({"role": "assistant", "content": text})
            # The critical recovery: a turn that researched candidates but returned
            # prose owes the user a spec, not a shortlist dressed up as a finished
            # benchmark. The same model just declined to emit one, so asking it
            # again is the correlated-laziness trap; a DISTINCT supervisor is asked
            # once instead, and only its parser-validated spec is accepted.
            if spec is None and self._should_recover_spec(text):
                recovered = self._recover_spec_via_supervisor(text)
                if recovered is None:
                    # No distinct supervisor, or it produced nothing valid. Say so
                    # honestly and actionably rather than present the prose as done.
                    self._delta(SPEC_RECOVERY_UNAVAILABLE)
                    return
                spec = recovered
            self._delta(text)
            if spec:
                # Both gates run before the elimination pass, so anything they
                # add is on the shortlist when the review sees it.
                spec = self._ensure_discovery_reach(spec)
                spec = self._ensure_build_path(spec)
                spec = self._review_shortlist(spec)
                self.emit("artifact", {"kind": "spec", "spec": spec})
                self._state("SPEC_CONFIRM")
            return

        # Unreachable in practice: the final round is toolless, so the model has
        # nothing to do but reply. Kept as a truthful last resort rather than
        # silence, and it says what happened instead of blaming the request.
        self._delta(
            "I could not settle on a benchmark spec within this turn. "
            "Tell me which tools to compare and I will propose one."
        )

    def _prepare_brief(self, user_message: str) -> tuple[str, dict | None]:
        """Restate the opening request as a research brief.

        Returns the rendered prompt block and the parsed brief, or ("", None).
        The caller needs the parsed form as well as the text: an incomplete brief
        becomes a confirmation card before any searching happens.

        One toolless completion, run before the intake loop so it costs none of
        the round budget. It is strictly an amplifier: on any failure the turn
        proceeds on the request exactly as the user wrote it, because a brief is
        an organisational aid and losing one costs the user nothing. The rendered
        block says as much in its first line, so the model treats the user's own
        words as authoritative wherever the two differ.
        """
        def _fail_open(detail: str) -> None:
            self.emit("artifact", {
                "kind": "trace", "tool": "prompt_brief",
                "args_summary": "opening request",
                "status": "error",
                "detail": f"{detail}; proceeding with the request as written",
            })

        messages = [{"role": "system", "content": PROMPT_BRIEF_SYSTEM},
                    {"role": "user", "content": user_message}]
        brief = None
        # One repair round, mirroring the assessment retry: the parse failure
        # travels back verbatim so the second attempt can correct rather than
        # repeat. A dead provider gets no retry — repair fixes replies, not
        # outages — and the trace says which of the two actually happened.
        for repairing in (False, True):
            try:
                resp = _orchestrator_complete(
                    self.runtime_env, messages=messages, temperature=0,
                )
                content = str(resp.choices[0].message.content or "")
            except _RunCancelled:
                raise
            except Exception:
                _fail_open("model call failed")
                return "", None
            try:
                brief = _build_prompt_brief(content, user_message)
                break
            except Exception as exc:
                if repairing:
                    _fail_open("reply unparseable after repair")
                    return "", None
                # An empty reply has nothing to show back, and Anthropic-style
                # backends reject an assistant turn with empty content — the
                # repair would then die on transport, not on the model.
                replay = ([{"role": "assistant", "content": content}]
                          if content.strip() else [])
                messages = messages + replay + [
                    {"role": "user", "content":
                        "IMPORTANT: that reply failed validation "
                        f"({exc}). Return ONLY the JSON object, with every "
                        "documented key present."},
                ]
        constraints = brief["constraints"]
        must_have = constraints.get("must_have") or []
        self.emit("artifact", {
            "kind": "trace", "tool": "prompt_brief",
            "args_summary": brief["category"] or "opening request",
            "status": "ok",
            "detail": (f"{len(must_have)} stated must-have"
                       f"{'' if len(must_have) == 1 else 's'}, "
                       f"{len(brief['unknowns'])} unknown"
                       f"{'' if len(brief['unknowns']) == 1 else 's'}"),
        })
        lines = [
            "The request, restated as a research brief. The user's own message remains "
            "authoritative; this brief only organizes it.",
        ]
        if brief["category"]:
            lines.append(f"Category: {brief['category']}")
        if brief["objective"]:
            lines.append(f"Objective: {brief['objective']}")
        # Imported here like every other tool_assessment use in this module, so
        # the brief renders constraints in exactly the wording the assessment
        # prompt uses for the same object.
        from engine.tool_assessment import format_constraints

        stated = format_constraints(constraints)
        if stated:
            lines.append(f"Stated constraints: {'; '.join(stated.splitlines())}")
        if brief["inferred_context"]:
            # Declared, never smuggled. An assumption the user can read is one
            # they can correct before a spec is built on it; the same assumption
            # applied silently is just a requirement they never asked for.
            lines.append(
                "Working assumptions inferred from their wording (state these plainly in "
                "your reply so the user can correct them before the spec is confirmed; a "
                "corrected assumption outranks this brief):"
            )
            lines.extend(
                f"- {item['assumption']} (from: {item['basis']})"
                for item in brief["inferred_context"]
            )
        if brief["unknowns"]:
            lines.append(
                "Unknown (do not assume — ask if it would change the shortlist, "
                f"otherwise proceed and say what you assumed): {', '.join(brief['unknowns'])}"
            )
        if brief["search_angles"]:
            lines.append(f"Search angles: {'; '.join(brief['search_angles'])}")
        # The spec's constraints object is an audit record of what the user
        # actually said. An inference that leaked into it would be indistinguishable
        # from a stated requirement the moment the spec left this turn.
        lines.append(
            "Do not copy assumptions into the spec's constraints; only what the user "
            "stated or confirms belongs there."
        )
        return "\n".join(lines), brief

    def _extract_spec(self, text: str) -> dict | None:
        m = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
        if not m:
            return None
        try:
            spec = json.loads(_strip_json_comments_and_trailing_commas(m.group(1)))
        except json.JSONDecodeError:
            return None
        request_text = "\n".join(
            str(message.get("content") or "")
            for message in self._messages
            if isinstance(message, dict) and message.get("role") == "user"
        )
        return _normalize_intake_spec(spec, self.dataset_available, request_text)

    def _should_recover_spec(self, text: str) -> bool:
        """A researched turn that owed a spec but returned prose, not a question.

        The recovery exists for exactly one failure: after web research the
        primary describes a shortlist in prose instead of emitting the fenced
        spec, and the turn would otherwise end presenting that shortlist as if it
        were a finished benchmark. A turn that ran no web search is an ordinary
        conversational reply, and a reply that ends in a question is a genuine
        clarification — both are left untouched so the user still gets them
        verbatim rather than an unnecessary supervisor round.
        """
        if not self._turn_search_queries:
            return False
        stripped = (text or "").strip()
        return bool(stripped) and not stripped.endswith("?")

    def _recover_spec_via_supervisor(self, assistant_text: str) -> dict | None:
        """One distinct-model attempt to turn a researched turn into a valid spec.

        Deterministic code has already established the concrete violation — the
        reply carried no spec the parser accepts — so the supervisor receives
        that violation, the exact spec contract, the prose to correct, and the
        conversation and findings it may draw from. Its reply is accepted ONLY
        through the same _extract_spec/_normalize_intake_spec the primary path
        uses, with no new searches and exactly one attempt. Returns the
        normalized spec, or None when no distinct supervisor is configured or its
        reply does not validate.
        """
        from engine import supervisor

        contract = (spec_recovery_contract_extraction(self.dataset_fields) if self.dataset_available
                    else SPEC_RECOVERY_CONTRACT_TOOL_ASSESSMENT)
        conversation = "\n".join(
            f"{message.get('role')}: {message.get('content')}"
            for message in self._messages
            if isinstance(message, dict)
            and message.get("role") in ("user", "assistant")
            and isinstance(message.get("content"), str)
            and message.get("content")
        )
        digest = _findings_digest(self.findings)
        context = f"{conversation}\n\n{digest}" if digest else conversation
        request = supervisor.SupervisionRequest(
            task="spec_recovery",
            contract=contract,
            violations=[
                "the reply completed a researched turn without a fenced json "
                "benchmark spec, so no benchmark could be run",
            ],
            artifact=assistant_text,
            context=context,
        )
        outcome = supervisor.supervise(
            request,
            primary_capability="orchestration",
            validate=self._extract_spec,
            env=self.runtime_env,
            redact=self._redact,
            # The prose being corrected was produced by the last orchestration
            # completion — after failover that is not the configured primary, so
            # the supervisor must be told who actually wrote it.
            exclude=[self._last_orchestration_producer],
        )
        self.emit("artifact", supervisor.trace_artifact(request, outcome))
        if outcome.corrected and outcome.identity is not None:
            # The recovered spec is authored by the supervisor. Any later
            # shortlist review must exclude that identity as well as the
            # orchestration models whose research fed the draft.
            self._last_orchestration_producer = outcome.identity
            self._orchestration_producers.add(outcome.identity)
        return outcome.parsed if outcome.corrected else None

    def _review_completion(self, system: str, user: str):
        """One toolless, temperature-0 review on a DISTINCT model, or a skip.

        Returns ``(content, identity)`` where ``identity`` is the supervisor
        ``ModelIdentity`` that served the call. When no distinct supervisor is
        configured — including when every candidate reviewer is a model that
        actually produced this turn's shortlist — it returns ``(None, None)`` and
        makes NO call. It never falls back to the primary orchestrator: a review
        by the model that produced the artifact is the correlated self-review the
        whole mechanism exists to prevent, so the honest answer is to skip and let
        the caller keep the artifact unreviewed rather than fake independence.
        """
        from engine.llm_clients import chat_client, supervisor_identity

        identity = supervisor_identity(
            "orchestration", self.runtime_env,
            exclude=self._orchestration_producers)
        if identity is None:
            return None, None
        resp = chat_client(identity.provider, self.runtime_env).chat.completions.create(
            model=identity.model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            temperature=0)
        return (resp.choices[0].message.content or ""), identity

    def _build_plan(self, metrics: dict, spec: dict) -> dict | None:
        """An architecture over the assessed components, when building is the answer.

        Only when every marketed product failed the requirement and a component
        did not: a plan offered beside a product that works would be advice the
        evidence does not support. Returns None on any failure — the plan is the
        most useful part of a no-product verdict and also the most optional, so
        it can never cost the run its report.
        """
        from engine import build_plan
        from engine.tool_assessment import build_path_is_the_answer

        components = build_path_is_the_answer(metrics)
        if not components:
            return None
        failure: dict = {}
        plan = build_plan.generate(
            spec.get("objective") or spec.get("category") or "",
            spec.get("constraints") or {},
            components,
            env=self.runtime_env,
            failure=failure,
        )
        self.emit("artifact", {
            "kind": "trace", "tool": "build_plan",
            "args_summary": f"{len(components)} components",
            "status": "ok" if plan else "error",
            "detail": (f"planned over {', '.join(n for n, _ in components)}"
                       if plan else "no implementation plan produced: "
                       + failure.get("detail", "unknown failure")),
        })
        return plan

    _MAX_SPEC_CANDIDATES = 20

    def _ensure_discovery_reach(self, spec: dict) -> dict:
        """Make sure the sweep actually reached the two pools rule 3a names.

        The intake prompt asks for a category-ladder query and an app-store
        channel query. On a live run the model ran the ladder query, silently
        skipped the channel query, and dropped the objective's distinguishing
        requirement term from most of the sweep. That combination — channel
        shape, one rung up, requirement terms kept — is the only dialect that
        surfaced a known store-first consumer product, and it came back at
        position 1, so what was missed was not a marginal name.

        Instructions are honoured probabilistically; this is enforced instead.
        Two extra completions, offered no tools, and up to two searches this code
        dispatches itself. Every failure path returns the spec as drafted,
        because a gate that could lose a shortlist would cost more than it
        earns — and that holds per query: one query the reach reply got wrong
        never cancels the search the other one still buys.
        """
        if spec.get("benchmark_type") != "tool_assessment":
            return spec
        candidates = spec.get("candidates") or []
        excluded = spec.get("excluded") if isinstance(spec.get("excluded"), list) else []
        taken = {str(c.get("name") or "") for c in candidates if isinstance(c, dict)}
        taken |= {str(e.get("name") or "") for e in excluded if isinstance(e, dict)}
        request = json.dumps({
            "objective": spec.get("objective", ""),
            "category": spec.get("category", ""),
            "constraints": spec.get("constraints") or {},
            "shortlisted": [
                (c.get("display_name") or c.get("name") or "")
                for c in candidates if isinstance(c, dict)
            ],
        }, indent=2)
        try:
            resp = self._orchestration_complete(
                messages=[{"role": "system", "content": DISCOVERY_REACH_SYSTEM},
                          {"role": "user", "content": request}],
                temperature=0,
            )
            ladder_query, channel_query = _parse_discovery_queries(
                resp.choices[0].message.content
            )
        except _RunCancelled:
            raise
        except Exception as exc:
            self.emit("artifact", {
                "kind": "trace", "tool": "discovery_reach",
                "args_summary": f"{len(candidates)} candidates",
                "status": "error",
                "detail": f"{type(exc).__name__}: no supplementary searches; shortlist kept as drafted",
            })
            return spec

        # Both queries always run. Skipping the channel query because intake ran
        # "a channel-shaped query" was measured to skip the wrong one: intake's
        # own channel query dropped the requirement terms ("math practice app
        # ios") and returned kids-app listicles, while the gate's — which is
        # REQUIRED to keep those terms — was the one that never executed. The
        # gate cannot mechanically judge a prior query's quality, only its
        # shape, so shape-matching is not grounds to skip. One extra search is
        # the cost; the store-first pool going unsearched is the alternative.

        from engine import docs_intel
        from engine.tools import WEB_SEARCH_RESULTS

        rows: list[dict] = []
        seen_urls: set[str] = set()
        ran = 0
        for source, query in (("ladder", ladder_query), ("channel", channel_query)):
            if not query:
                # The parse salvaged the other query; an empty one has no pool
                # to reach and is skipped the way a failed search is.
                continue
            self._check_cancelled()
            try:
                results = docs_intel.web_search(
                    query, n=WEB_SEARCH_RESULTS, env=self.runtime_env
                )
            except _RunCancelled:
                raise
            except Exception:
                # A failed search skips. One reachable pool is still more than
                # the sweep had.
                continue
            ran += 1
            for row in results if isinstance(results, list) else []:
                if not isinstance(row, dict):
                    continue
                url = str(row.get("url") or "").strip()
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                # Provenance travels to the harvest: a channel row is a store
                # listing whose title speaks the end user's activity words, and
                # the harvest must not demand it restate the objective.
                rows.append({"title": str(row.get("title") or url).strip(), "url": url,
                             "source": source})
        if not rows:
            self.emit("artifact", {
                "kind": "trace", "tool": "discovery_reach",
                "args_summary": f"{len(candidates)} candidates",
                "status": "ok",
                "detail": f"ran {ran} supplementary searches; added none",
            })
            return spec
        # The results persist the way the intake loop's own searches do, so a
        # later turn inherits them instead of researching the same ground.
        self.findings.extend(rows[:_MAX_FINDINGS_IN_PROMPT])

        harvest_rows = rows[:_MAX_FINDINGS_IN_PROMPT]
        harvest_request = json.dumps({
            "objective": spec.get("objective", ""),
            "already_listed": sorted({
                *(str(c.get("display_name") or c.get("name") or "")
                  for c in candidates if isinstance(c, dict)),
                *(str(e.get("display_name") or e.get("name") or "")
                  for e in excluded if isinstance(e, dict)),
            } - {""}),
            "results": harvest_rows,
        }, indent=2)
        try:
            resp = self._orchestration_complete(
                messages=[{"role": "system", "content": DISCOVERY_HARVEST_SYSTEM},
                          {"role": "user", "content": harvest_request}],
                temperature=0,
            )
            added = _parse_discovery_candidates(
                resp.choices[0].message.content,
                taken,
                {row["url"] for row in harvest_rows},
            )
        except _RunCancelled:
            raise
        except Exception as exc:
            self.emit("artifact", {
                "kind": "trace", "tool": "discovery_reach",
                "args_summary": f"{len(candidates)} candidates",
                "status": "error",
                "detail": f"{type(exc).__name__}: {ran} searches ran; shortlist kept as drafted",
            })
            return spec

        room = max(0, self._MAX_SPEC_CANDIDATES - len(candidates))
        added = added[:room]
        if added:
            spec = dict(spec)
            spec["candidates"] = [*candidates, *added]
        self.emit("artifact", {
            "kind": "trace", "tool": "discovery_reach",
            "args_summary": f"{len(candidates)} candidates",
            "status": "ok",
            "detail": (
                f"ran {ran} supplementary searches; added "
                + (", ".join(c["display_name"] for c in added) if added else "none")
            ),
        })
        return spec

    def _ensure_build_path(self, spec: dict) -> dict:
        """Make sure the shortlist carries something to build with.

        The intake prompt asks for build components unconditionally, and the
        model ignores it: re-running one real question twice, the spec came back
        with three products and no components, then four and none. A shortlist
        of products only has a failure mode with no exit — when every product is
        later rated not implementable, the run ends telling the user what does
        not work and nothing about what would, and the spec is frozen by then.

        So it is enforced here rather than asked for: exactly one extra
        completion, offered no tools, run only when the drafted spec has no
        component. Every failure path returns the spec as drafted, because a
        gate that could lose a shortlist would cost more than it earns.
        """
        if spec.get("benchmark_type") != "tool_assessment":
            return spec
        candidates = spec.get("candidates") or []
        if any(c.get("role") == "build_component" for c in candidates):
            return spec
        request = json.dumps({
            "objective": spec.get("objective", ""),
            "category": spec.get("category", ""),
            "constraints": spec.get("constraints") or {},
            "existing_candidates": [c.get("display_name") or c["name"] for c in candidates],
        }, indent=2)
        try:
            resp = self._orchestration_complete(
                messages=[{"role": "system", "content": BUILD_PATH_SYSTEM},
                          {"role": "user", "content": request}],
                temperature=0,
            )
            added = _parse_build_components(
                resp.choices[0].message.content,
                {c["name"] for c in candidates},
            )
        except _RunCancelled:
            raise
        except Exception as exc:
            self.emit("artifact", {
                "kind": "trace", "tool": "build_path",
                "args_summary": f"{len(candidates)} products",
                "status": "error",
                "detail": f"{type(exc).__name__}: no build path added; shortlist kept as drafted",
            })
            return spec
        if not added:
            self.emit("artifact", {
                "kind": "trace", "tool": "build_path",
                "args_summary": f"{len(candidates)} products",
                "status": "ok", "detail": "no documented building blocks proposed",
            })
            return spec
        spec = dict(spec)
        spec["candidates"] = [*candidates, *added]
        self.emit("artifact", {
            "kind": "trace", "tool": "build_path",
            "args_summary": f"{len(candidates)} products",
            "status": "ok",
            "detail": "added " + ", ".join(c["display_name"] for c in added),
        })
        return spec

    def _review_shortlist(self, spec: dict) -> dict:
        """One bounded elimination pass over a drafted tool_assessment shortlist.

        Exactly one extra completion, offered no tools, run only when there is a
        field to narrow (two or more candidates). Every failure path returns the
        spec as drafted: this gate improves the audit record, and a gate that
        could lose a run's shortlist would cost more than it earns. Dropped
        candidates are persisted on the spec as ``excluded`` with the stated
        constraint each violated, so the report can show why the field is the
        field.
        """
        if spec.get("benchmark_type") != "tool_assessment":
            return spec
        candidates = spec.get("candidates") or []
        if len(candidates) < 2:
            return spec
        shortlist = [
            {"name": c["name"], "display_name": c.get("display_name") or c["name"],
             "kind": c.get("kind", ""), "docs_url": c.get("docs_url", ""),
             "role": c.get("role", "product")}
            for c in candidates
        ]
        review_request = json.dumps({
            "objective": spec.get("objective", ""),
            "constraints": spec.get("constraints") or {},
            "candidates": shortlist,
        }, indent=2)
        try:
            # An elimination pass corrects a model-authored shortlist, so the
            # model that drafted it is the wrong one to sit in judgement of it.
            # When no DISTINCT supervisor is configured the correction is skipped
            # honestly — never handed back to the orchestrator that drafted the
            # shortlist, which would be exactly the correlated self-review this
            # exists to prevent. The skip is a healthy optional-review outcome,
            # not a pipeline failure, so it is traced ok and the field is kept.
            content, reviewer = self._review_completion(
                SHORTLIST_REVIEW_SYSTEM, review_request)
            if reviewer is None:
                self.emit("artifact", {
                    "kind": "trace", "tool": "shortlist_review",
                    "args_summary": f"{len(candidates)} candidates",
                    "status": "ok",
                    "detail": ("no distinct supervisor configured; the shortlist is "
                               "kept as drafted without an independent review"),
                })
                return spec
            drops = _parse_shortlist_verdict(
                content, {c["name"] for c in candidates},
            )
        except _RunCancelled:
            raise
        except Exception as exc:
            self.emit("artifact", {
                "kind": "trace", "tool": "shortlist_review",
                "args_summary": f"{len(candidates)} candidates",
                "status": "error",
                "detail": f"{type(exc).__name__}: review unavailable; shortlist kept as drafted",
            })
            return spec
        if not drops:
            self.emit("artifact", {
                "kind": "trace", "tool": "shortlist_review",
                "args_summary": f"{len(candidates)} candidates",
                "status": "ok", "detail": "no stated constraint rules a candidate out",
            })
            return spec
        dropped_names = {d["name"] for d in drops}
        keep = [c for c in candidates if c["name"] not in dropped_names]
        if not keep:
            # A verdict that empties the field never becomes the spec, but
            # staying silent about it crowns the least-bad failure later: a
            # real run benchmarked four tools that all missed a stated key
            # requirement and the report still announced a winner. The field
            # is kept, and the user is told before spending a run on it.
            by_name = {c["name"]: c for c in candidates}
            notes = "\n".join(
                f"- {by_name[d['name']].get('display_name') or d['name']}: {d['violates']}"
                for d in drops
            )
            self.emit("artifact", {
                "kind": "trace", "tool": "shortlist_review",
                "args_summary": f"{len(candidates)} candidates",
                "status": "ok",
                "detail": "every candidate violates a stated constraint; shortlist kept",
            })
            self._delta(
                "\nWarning: every candidate on this shortlist appears to violate a "
                "stated constraint:\n"
                f"{notes}\n"
                "You can still run the benchmark to compare them on documentation "
                "evidence, or name different candidates before running.\n"
            )
            return spec
        by_name = {c["name"]: c for c in candidates}
        excluded = [
            {"name": d["name"],
             "display_name": str(by_name[d["name"]].get("display_name") or d["name"]),
             "violates": d["violates"]}
            for d in drops
        ]
        reviewed = dict(spec)
        reviewed["candidates"] = keep
        # Intake may already have recorded what discovery surfaced and did not
        # shortlist. Those entries are the other half of the same record, so the
        # constraint drops are appended to them rather than written over them.
        prior = [item for item in (spec.get("excluded") or [])
                 if isinstance(item, dict) and item.get("name") not in dropped_names]
        reviewed["excluded"] = prior + excluded
        # Say who reviewed only when it was genuinely independent; a same-identity
        # fallback review is never dressed up as one.
        reviewer_note = f" (independent supervisor {reviewer.label()})" if reviewer else ""
        self.emit("artifact", {
            "kind": "trace", "tool": "shortlist_review",
            "args_summary": f"{len(candidates)} candidates",
            "status": "ok",
            "detail": f"{len(excluded)} dropped against stated constraints{reviewer_note}",
        })
        notes = "\n".join(
            f"- {item['display_name']}: {item['violates']}" for item in excluded
        )
        self._delta(
            f"\nDropped from the shortlist against your stated constraints:\n{notes}\n"
        )
        return reviewed

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

    def run_tool_assessment(self, spec: dict) -> dict:
        """Run a documentation assessment with lifecycle isolation."""
        return self._run_with_cleanup(self._run_tool_assessment_impl, spec)

    def _run_tool_assessment_impl(self, spec: dict) -> dict:
        """Assess arbitrary tools from docs and execute only viable integrations."""
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
        constraints = spec.get("constraints") if isinstance(spec.get("constraints"), dict) else {}
        metrics: dict[str, dict] = {}
        statuses = {str(candidate.get("name") or "candidate"): "pending" for candidate in candidates}
        self._state("DOCS_INTEL", statuses)
        scraped_candidates: list[dict[str, str]] = []
        candidate_by_name = {
            str(candidate.get("name") or "candidate"): candidate for candidate in candidates
        }

        def gather(candidate_spec: dict):
            """Fetch one candidate's documentation. Returns (name, entry, error).

            Every candidate's fetch is an independent network wait, so they run
            concurrently: serially this phase cost the sum of every vendor's
            page latency and dominated the run (measured 399s of a 445s
            assessment across seven candidates).
            """
            self._check_cancelled()
            name = str(candidate_spec.get("name") or "candidate")
            display_name = str(candidate_spec.get("display_name") or name)
            docs_url = str(candidate_spec.get("docs_url") or "").strip()
            if not docs_url:
                return name, None, "missing"

            try:
                # One retry before the candidate is written off. The scrape
                # already fails over across providers internally, but a whole
                # chain can lose one race (rate limit, slow render) and a
                # candidate's entire assessment should not hang on a single
                # attempt of anything.
                docs_value = None
                for attempt in (1, 2):
                    try:
                        scraped = dispatch_tool("scrape_docs", {"url": docs_url}, self.ctx)
                        docs_value = json.loads(scraped)
                        if isinstance(docs_value, dict) and docs_value.get("error"):
                            raise RuntimeError(str(docs_value["error"]))
                        break
                    except _RunCancelled:
                        raise
                    except Exception:
                        if attempt == 2:
                            raise
                        self._check_cancelled()
                docs_text = str(docs_value)
                safe_docs_url = self._redact(docs_url)
                for citation in self.ctx.citations:
                    if citation.get("url") == safe_docs_url:
                        citation["title"] = self._redact(
                            f"{display_name} documentation"
                        )
                entry = {"name": name, "docs_text": docs_text,
                         # The assessor must know a part from a product: a
                         # component is judged on covering ITS part of the
                         # objective, never the whole of it.
                         "role": str(candidate_spec.get("role") or "product")}
                # The pricing page feeds one assessment axis. A blocked or
                # missing page is not evidence about the tool: the axis is
                # withheld (null) rather than scored low, so this fetch is
                # allowed to fail without a trace of drama.
                pricing_url = str(candidate_spec.get("pricing_url") or "").strip()
                if pricing_url:
                    try:
                        scraped_pricing = dispatch_tool(
                            "scrape_docs", {"url": pricing_url}, self.ctx
                        )
                        pricing_value = json.loads(scraped_pricing)
                        if isinstance(pricing_value, dict) and pricing_value.get("error"):
                            raise RuntimeError(str(pricing_value["error"]))
                        entry["pricing_text"] = str(pricing_value)
                        safe_pricing_url = self._redact(pricing_url)
                        for citation in self.ctx.citations:
                            if citation.get("url") == safe_pricing_url:
                                citation["title"] = self._redact(
                                    f"{display_name} pricing"
                                )
                    except _RunCancelled:
                        raise
                    except Exception:
                        pass
                statuses[name] = "queued"
                self._state("DOCS_INTEL", dict(statuses))
                return name, entry, None
            except _RunCancelled:
                raise
            except Exception as exc:
                return name, None, type(exc).__name__

        with ThreadPoolExecutor(
                max_workers=max(1, min(DOCS_CONCURRENCY, len(candidates) or 1))) as ex:
            gathered = list(ex.map(_settled(gather), candidates))

        # Results are applied in candidate order: added concurrency must not
        # reorder the assessment batch or the citations that travel with it.
        for candidate_spec, (result, failure) in zip(candidates, gathered):
            name = str(candidate_spec.get("name") or "candidate")
            if failure is not None:
                if isinstance(failure, _RunCancelled):
                    raise failure
                error = type(failure).__name__
            else:
                name, entry, error = result
                if entry is not None:
                    scraped_candidates.append(entry)
                    continue
            if error == "missing":
                metrics[name] = unavailable_result(
                    "No official implementation documentation URL was provided.")
                statuses[name] = "skipped"
                self._state("DOCS_INTEL", dict(statuses))
                continue
            metrics[name] = unavailable_result(
                f"Documentation scrape failed: {error}")
            statuses[name] = "skipped"
            self.emit("artifact", {
                "kind": "trace",
                "tool": "scrape_docs",
                "args_summary": name,
                "status": "error",
                "detail": f"{error}: documentation scrape failed",
            })
            self._state("DOCS_INTEL", dict(statuses))

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
                    constraints=constraints,
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
                self._delta(f"{display_name}: documentation was insufficient for a credible implementation. Execution skipped.\n")
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
                    "Not executable without credentials, so it was not executed.\n"
                )
                continue

            handle = None
            verification_status = "failed"
            try:
                statuses[name] = "provisioning"
                self._state("PROVISIONING", dict(statuses))
                handle = self.pool.acquire(name)
                self._log(name, "Sandbox allocated from documented implementation plan", "building")
                statuses[name] = "building"
                self._state("BUILDING", dict(statuses))
                for command in plan["build_commands"]:
                    self._check_cancelled()
                    self._log(name, f"$ {command}", "building")
                    output = self._stream_command(name, handle, command, "building")
                    self._sandbox_file(name, output, revision=1,
                                       path=_build_log_path(command))

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
        # The human name travels with the row. Metrics are keyed by slug, and
        # every consumer downstream — console, markdown, PDF — had only that key
        # to print, so a report about "Azure AI Search + Azure OpenAI" read
        # "azure_ai_search_openai" throughout. Stamped once here rather than at
        # each of the six places a row is built, so no branch can miss it.
        for name, values in metrics.items():
            values["display_name"] = _spec_display_name(spec, name)
            values["role"] = _spec_role(spec, name)
        # Every step was validated; the finished rows never were. This reads
        # them back, re-assesses whatever argues with itself, and keeps what
        # still does as a published caveat. It runs before redaction so the
        # repair sees the same documents the first assessment did, and it can
        # never fail the run: a consistency review that erases real measured
        # evidence is worse than one that never ran.
        surviving_flags: list[dict] = []
        try:
            from engine.self_check import run_self_check

            outcome = run_self_check(
                metrics,
                objective,
                {candidate["name"]: candidate for candidate in scraped_candidates},
                env=self.runtime_env,
                constraints=constraints,
            )
            surviving_flags = outcome["flags"]
            detail = (f"repaired {len(outcome['repaired'])}; "
                      f"{len(surviving_flags)} flags remain")
            # Name the independent reviewer when one actually ran, never implying
            # independence the deployment did not configure.
            if outcome.get("supervisor"):
                detail = f"{detail} (independent supervisor {outcome['supervisor']})"
            self.emit("artifact", {
                "kind": "trace",
                "tool": "self_check",
                "args_summary": f"{len(metrics)} rows",
                "status": "ok",
                "detail": detail,
            })
            with open(os.path.join(self.run_dir, "self_check.json"), "w",
                      encoding="utf-8") as handle:
                json.dump(self._redact_data(outcome), handle, indent=2)
        except _RunCancelled:
            raise
        except Exception as exc:
            self.emit("artifact", {
                "kind": "trace",
                "tool": "self_check",
                "args_summary": f"{len(metrics)} rows",
                "status": "error",
                "detail": f"{type(exc).__name__}: self-check failed",
            })
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
            excluded = spec.get("excluded") if isinstance(spec.get("excluded"), list) else []
            report = write_assessment_report(
                metrics, safe_citations, os.devnull,
                excluded=self._redact_data(excluded),
                build_plan=self._build_plan(metrics, spec),
                self_check=self._redact_data(surviving_flags),
            )
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
        # Every candidate at once. The account's concurrent-memory budget is the
        # real ceiling and the pool already queues behind it, so a second, lower
        # limit here only made wide comparisons run single file for no reason.
        self.pool.size = max(1, len(spec["candidates"]))
        # Prove a sandbox can be created before paying for docs intelligence and
        # adapter generation across every candidate. A provider that refuses
        # outright (no region entitlement, bad credentials) otherwise fails only
        # after that spend, once per candidate.
        self.pool.preflight()
        self._prepare_generated_adapters(spec["candidates"])
        self.pool.start()
        # Documentation scoring reads only the spec, so it runs beside execution
        # instead of after it. It starts only once the pool is warm: its scraping
        # turns ~170KB pages into text, which is CPU-bound and holds the GIL, and
        # starting it earlier measurably starved sandbox provisioning (4s -> 43s)
        # for a saving that was smaller than the loss.
        self._begin_research_scores(spec)
        self._run_candidates_scripted(spec["candidates"], images)
        return self._evaluate_and_report(ground_truth)

    def _run_candidates_scripted(self, candidates: list[dict], images: list[str]) -> None:
        with ThreadPoolExecutor(max_workers=max(1, len(candidates))) as ex:
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

        pending = [
            spec for spec in candidates
            if not (spec.get("use_fallback", True)
                    and is_builtin_adapter(str(spec.get("name") or "")))
        ]
        if not pending:
            return

        def prepare(candidate_spec: dict) -> tuple[str, str, object | None]:
            """Scrape and generate one adapter. Returns (name, docs_url, adapter).

            Runs on a worker thread and touches no shared engine state: every
            mutation of ``ctx`` is applied by the caller in candidate order, so
            added concurrency cannot reorder citations or generated adapters.
            """
            name = str(candidate_spec.get("name") or "")
            docs_url = str(candidate_spec.get("docs_url") or "")
            self._state("DOCS_INTEL", {name: "fetching"})
            if not docs_url:
                raise ValueError("documentation URL is required for generated adapters")
            docs = scrape_page(docs_url, env=self.runtime_env)
            self._state("ADAPTER_GEN", {name: "generating"})
            generated = generate_adapter(name, docs, env=self.runtime_env,
                                         fields=self.ctx.spec_fields)
            generated.name = name
            generated.docs_url = docs_url
            generated.pricing_url = str(candidate_spec.get("pricing_url") or "")
            generated.kind = str(candidate_spec.get("kind") or generated.kind)
            return name, docs_url, generated

        # Each candidate's scrape and codegen are independent and almost entirely
        # spent waiting on HTTP, so running them serially made preparation cost
        # the sum of every vendor's page latency rather than the slowest one.
        with ThreadPoolExecutor(max_workers=min(DOCS_CONCURRENCY, len(pending))) as ex:
            outcomes = list(ex.map(_settled(prepare), pending))

        for candidate_spec, (result, error) in zip(pending, outcomes):
            name = str(candidate_spec.get("name") or "")
            if error is not None:
                self.emit("artifact", {"kind": "trace", "tool": "generate_adapter",
                                       "args_summary": name, "status": "error",
                                       "detail": f"{type(error).__name__}: adapter preparation failed"})
                continue
            _, docs_url, generated = result
            self.ctx.citations.append({"title": f"{name} documentation", "url": docs_url})
            self.ctx.candidates[name] = generated
            self.emit("artifact", {"kind": "trace", "tool": "generate_adapter",
                                   "args_summary": name, "status": "ok"})

    # ------------------------------------------------------------- pipeline blocks
    def _candidate_snapshot(self, name: str, candidate) -> str | None:
        """A prebuilt snapshot for this candidate's exact build, if available.

        Only first-party adapters qualify: their build commands are ProofBench's
        own source, so a snapshot of them is reproducible and safe to reuse. A
        model-generated adapter's commands change per run, which would mean
        building a snapshot that is used exactly once.
        """
        from engine.builtin_adapters import is_builtin_adapter

        if not str(self.runtime_env.get("DAYTONA_API_KEY") or "").strip():
            return None
        # A first-party candidate has reproducible build commands, so it gets a
        # snapshot of its exact build. Everything else — a generated adapter for
        # a tool ProofBench has never seen — starts from the shared base, whose
        # toolchain and CUDA runtime are the slowest part of most installations
        # and are identical whatever the tool turns out to be.
        specific = (getattr(candidate, "build_commands", None)
                    and is_builtin_adapter(name))
        key = name if specific else "__base__"
        cached = self._snapshot_cache.get(key)
        if cached is not None:
            return cached or None
        try:
            from engine.snapshots import ensure_base_snapshot, ensure_snapshot

            client = self.pool._client()
            if specific:
                resolved = ensure_snapshot(
                    client, name, candidate.build_commands,
                    cpu=self.pool.cpu, memory_gib=self.pool.memory_gib,
                    gpu=self.pool.gpu, gpu_type=self.pool.gpu_type)
            else:
                resolved = ensure_base_snapshot(
                    client, cpu=self.pool.cpu, memory_gib=self.pool.memory_gib,
                    gpu=self.pool.gpu, gpu_type=self.pool.gpu_type)
        except Exception:
            resolved = None
        self._snapshot_cache[key] = resolved or ""
        return resolved

    def _candidate_pipeline(self, cand_spec: dict) -> None:
        """Build → validate (repair up to MAX_ADAPTER_REPAIR_ATTEMPTS → fallback) → run, per candidate."""
        name = cand_spec["name"]
        try:
            candidate = self._resolve_candidate(cand_spec)
            if candidate is None:
                self._fail_candidate(name, "no adapter available for this candidate")
                return
            # A first-party candidate's dependencies are the same on every run,
            # so they are baked into a snapshot once and the per-run install is
            # skipped entirely. Falls back to installing in-sandbox when no
            # snapshot could be produced.
            snapshot = self._candidate_snapshot(name, candidate)
            handle = self.pool.acquire(name, snapshot=snapshot) if snapshot \
                else self.pool.acquire(name)
            self._handle_to_candidate[handle.id] = name
            try:
                self._log(handle.label, "sandbox created", "provisioning")
                self._sandbox_file(handle.label, candidate.adapter_code, revision=1)
                self._upload_dataset(handle)
                self._state("BUILDING", {name: "building"})
                if snapshot:
                    self._log(handle.label,
                              "dependencies preinstalled from snapshot; build skipped",
                              "building")
                else:
                    self._build(handle, candidate)
                self._state("VALIDATING", {name: "validating"})
                if not self._validate(handle, candidate):
                    # A trusted built-in already IS the first-party adapter, and
                    # a re-loaded copy would carry no credential entitlement.
                    # Retrying it would only manufacture a second failure.
                    fb = (None if name in self._trusted_candidate_names
                          else self._try_fallback(cand_spec))
                    if fb is None:
                        self._fail_candidate(name, self._validation_reason(candidate.name))
                        return
                    candidate = fb
                    self._sandbox_file(
                        handle.label,
                        candidate.adapter_code,
                        revision=1,
                        path="fallback_adapter.py",
                    )
                    # A fallback is a different adapter than the snapshot was
                    # built for, so its dependencies are installed for real.
                    self._build(handle, candidate)
                    if not self._validate(handle, candidate):
                        self._fail_candidate(name, self._validation_reason(candidate.name))
                        return
                self._state("RUNNING", {name: "running"})
                self._run_dataset(handle, candidate)
                self._state("RUNNING", {name: "done"})
            finally:
                self._log(handle.label, "deleting disposable sandbox", "cleanup")
                self.pool.release(handle)
                self._log(handle.label, "sandbox deleted; execution record retained", "cleanup")
        except _RunCancelled:
            raise
        except Exception as e:
            # The exception text is what makes this actionable ("no space left on
            # device", "image pull timed out"). It is redacted, so naming it costs
            # nothing that "candidate pipeline failed" was protecting.
            detail = self._redact(_readable_error(f"{type(e).__name__}: {e}"))
            self.emit(
                "artifact",
                {"kind": "trace", "tool": "pipeline", "args_summary": name,
                 "status": "error", "detail": detail},
            )
            self._fail_candidate(name, detail)

    def _validation_reason(self, candidate_name: str) -> str:
        """The adapter's own error, so metrics can name why a candidate did not run."""
        reason = self._last_validation_error.get(candidate_name)
        return f"adapter validation failed: {reason}" if reason else "adapter validation failed"

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
        # The run's extraction schema, as data. Adapters read it to know which
        # fields to produce, so one adapter source serves every schema instead
        # of baking the invoice columns into each candidate.
        schema = _schema_fields(self.ctx.spec_fields)
        schema_path = Path(self.run_dir) / f"pb_schema_{handle.id}.json"
        schema_path.write_text(json.dumps(schema), encoding="utf-8")
        try:
            self.pool.upload(handle, str(schema_path), "pb_schema.json")
        finally:
            schema_path.unlink(missing_ok=True)
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
            out = self._stream_command(handle.label, handle, cmd, "building")
            self._sandbox_file(handle.label, out, revision=1,
                               path=_build_log_path(cmd))

    def _probe_adapter(self, handle, candidate: Candidate, label: str) -> tuple[bool, str]:
        """Run one validation probe and log its outcome. Returns (ok, raw output)."""
        image_path = "images/" + self._first_image()
        self._log(handle.label, f"$ python adapter.py {image_path}  # {label}", "validating")
        code = self._adapter_code(candidate, image_path)
        out = self.pool.run_python(handle, code, timeout=180)
        ok = self._collate_probe(out)
        self._log(handle.label, f"{label}: " + ("ok" if ok else "FAILED"), "validating")
        if not ok:
            # Keep a bounded, secret-redacted diagnostic in the trace so a real
            # integration failure is actionable instead of collapsing to the
            # unhelpful phrase "adapter validation failed".
            diagnostic_lines = [line.strip() for line in self._redact(out).splitlines()
                                if line.strip()][-6:]
            for line in diagnostic_lines:
                self._log(handle.label, f"validation error: {line[:260]}", "validating")
            # The same diagnostic has to reach the metrics, or the report states
            # a zero score for a candidate that never ran.
            self._last_validation_error[candidate.name] = _adapter_error_summary(diagnostic_lines)
        return ok, out

    def _validate(self, handle, candidate: Candidate) -> bool:
        ok, out = self._probe_adapter(handle, candidate, "validation")
        if ok or candidate.name in self._trusted_candidate_names:
            return ok
        # Each attempt hands the sandbox's own latest error back to the model and
        # re-probes. The pipeline shape here never changes: this is still the
        # engine, scripted, deciding to call validate again — only the adapter
        # code under test differs between attempts.
        for attempt in range(1, MAX_ADAPTER_REPAIR_ATTEMPTS + 1):
            self._check_cancelled()
            repaired = self._repair_once(candidate, self._redact(out), attempt)
            if repaired is None:
                break
            candidate.adapter_code = repaired
            self._sandbox_file(handle.label, repaired, revision=attempt + 1)
            candidate.setup_complexity = min(5, candidate.setup_complexity + 1)
            ok, out = self._probe_adapter(handle, candidate, f"repair attempt {attempt}")
            if ok:
                break
        return ok

    def _repair_once(self, candidate: Candidate, error_output: str, attempt: int = 1) -> str | None:
        """Ask the codegen worker to fix the adapter. Returns new code or None."""
        from engine.llm_clients import capability_providers

        if not capability_providers("codegen", self.runtime_env):
            return None
        try:
            from engine.adapter_gen import repair_adapter

            repaired = repair_adapter(
                candidate.adapter_code, error_output[-2000:], env=self.runtime_env,
                fields=self.ctx.spec_fields,
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
        # One process for the whole dataset, always. Running a process per image
        # reloaded the candidate's runtime every time — for EasyOCR that was a
        # full model load per document (measured 12s of work behind a 23s cost).
        self._check_cancelled()
        self._log(
            handle.label,
            f"$ python adapter.py --batch {len(images)} images",
            "running",
        )
        # One process for the whole dataset. Splitting a local candidate across
        # worker processes was measured and rejected: an OCR runtime already
        # parallelises internally across the sandbox's CPUs, so four processes
        # each starting their own thread pool oversubscribed the same cores and
        # tripled the wall clock (220s -> 764s on 15 documents).
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
            metrics = evaluate_results(
                self.results_path, ground_truth, pricing=pricing,
                fields=(self._active_spec or {}).get("fields"),
            )
        if not metrics:
            raise RuntimeError(
                "real benchmark produced no valid result records; no metrics were generated"
            )
        # Same reason as the assessment path: the row travels with the name a
        # person would recognise, not just the slug it is keyed by.
        for name, values in metrics.items():
            if isinstance(values, dict):
                values["display_name"] = _spec_display_name(self._active_spec, name)
        self._attach_research_scores(metrics)
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

    # Background documentation scoring, started beside execution.
    _research_future = None
    _research_executor = None


    def _discard_research_future(self) -> None:
        """Release the background scorer, whichever way scoring ended."""
        executor, self._research_executor = self._research_executor, None
        self._research_future = None
        if executor is not None:
            executor.shutdown(wait=False)

    def _begin_research_scores(self, spec: dict) -> None:
        """Kick off documentation scoring in the background, if it applies.

        Best effort and never fatal: a failure here is collected at join time and
        reported exactly as the synchronous path reported it. When nothing is
        started, ``_attach_research_scores`` falls back to computing inline, so
        callers that never begin one (assessment runs, tests) are unaffected.
        """
        candidates = self._research_candidates(spec)
        if not candidates:
            return
        objective, constraints = self._research_arguments(spec)
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pb-research")
        self._research_executor = executor
        self._research_future = executor.submit(
            self._compute_research_scores, candidates, objective, constraints)

    def _research_candidates(self, spec: dict) -> list[dict]:
        """Spec candidates eligible for documentation scoring."""
        return [candidate for candidate in (spec.get("candidates") or [])
                if str(candidate.get("name") or "")]

    def _research_arguments(self, spec: dict):
        from engine.research_scores import extraction_objective

        constraints = (spec.get("constraints")
                       if isinstance(spec.get("constraints"), dict) else None)
        return extraction_objective(spec), constraints

    def _compute_research_scores(self, candidates, objective, constraints):
        from engine.research_scores import research_scores

        return research_scores(candidates, objective, env=self.runtime_env,
                               constraints=constraints)

    def _attach_research_scores(self, metrics: dict) -> None:
        """Score every candidate from its documentation, alongside the measurements.

        A run that executes two candidates out of six is not a comparison of
        six, and the four it could not execute are not bad products — they were
        unreachable, usually for a missing credential. The documentation is the
        evidence the run already had about them, so it is scored here on its own
        stated basis. Measured metrics are untouched: a research score never
        stands in for an accuracy this run did not observe.

        Best effort throughout. If no assessment provider is configured, or the
        docs cannot be fetched, the rows keep exactly the shape they had.
        """
        from engine.evaluate import SETUP_COMPLEXITY
        from engine.research_scores import (
            extraction_objective,
            merge_research_scores,
            research_scores,
        )

        spec = self._active_spec or {}
        candidates = [candidate for candidate in (spec.get("candidates") or [])
                      if str(candidate.get("name") or "") in metrics]
        if not candidates:
            self._discard_research_future()
            return
        self._check_cancelled()
        summary = f"{len(candidates)} documentation assessments"
        self.emit("artifact", {"kind": "trace", "tool": "research_scores",
                               "args_summary": summary, "status": "start"})
        pending = self._research_future
        try:
            if pending is not None:
                # Started alongside execution; by now it is usually already done.
                scored = pending.result()
            else:
                scored = research_scores(
                    candidates,
                    extraction_objective(spec),
                    env=self.runtime_env,
                    constraints=spec.get("constraints") if isinstance(spec.get("constraints"), dict) else None,
                )
        except _RunCancelled:
            raise
        except Exception as exc:
            self.emit("artifact", {"kind": "trace", "tool": "research_scores",
                                   "args_summary": summary, "status": "error",
                                   "detail": f"{type(exc).__name__}: research scoring unavailable"})
            return
        finally:
            self._discard_research_future()
        # Scoring covers every spec candidate; only the ones this run measured
        # are merged, so a background start can never widen the metrics table.
        scored = {name: value for name, value in (scored or {}).items()
                  if name in metrics}
        merge_research_scores(metrics, scored, curated_setup=SETUP_COMPLEXITY)
        self.emit("artifact", {"kind": "trace", "tool": "research_scores",
                               "args_summary": summary,
                               "status": "ok" if scored else "error",
                               "detail": f"{len(scored)} of {len(candidates)} scored from documentation"})

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
        # A hosted candidate spends every document waiting on an HTTP response,
        # so its documents run concurrently. A local runtime is CPU-bound and
        # its model is usually not thread-safe (EasyOCR's Reader is not), so it
        # stays sequential and gains its speed from loading the model once.
        workers = HOSTED_DOC_CONCURRENCY if candidate.kind == "hosted_api" else 1
        runner = f'''
import json as _pb_json, time as _pb_time
from concurrent.futures import ThreadPoolExecutor as _PbPool
import threading as _pb_threading

_pb_print_lock = _pb_threading.Lock()


def _pb_one(_pb_entry):
    _pb_doc_id, _pb_image = _pb_entry
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
    # One line per document, never interleaved: the collator parses by line.
    with _pb_print_lock:
        print("RESULT_JSON:" + _pb_json.dumps(_pb_result), flush=True)


_pb_documents = {documents!r}
_pb_workers = min({workers}, len(_pb_documents)) or 1
if _pb_workers > 1:
    with _PbPool(max_workers=_pb_workers) as _pb_ex:
        list(_pb_ex.map(_pb_one, _pb_documents))
else:
    for _pb_entry in _pb_documents:
        _pb_one(_pb_entry)
'''
        return env_prelude(
            code + "\n" + runner,
            self.ctx.env_passthrough,
            self._entitlements_for(candidate),
        )

    def _stream_command(self, sandbox: str, handle, command: str, phase: str,
                        timeout: int = 300) -> str:
        """Run a build command, showing its output while it runs.

        Lines reach the panel as the command produces them, which is the whole
        point: a two-minute install used to show one line and then everything
        at once, which reads as a hang. The live view is capped so a chatty
        install cannot evict the rest of the run from the session's bounded
        event log; the complete output is written as a file artifact, so
        nothing is actually lost.
        """
        emitted = 0
        suppressed = 0

        def on_line(line: str) -> None:
            nonlocal emitted, suppressed
            if not line.strip():
                return
            if emitted < SANDBOX_LOG_LINES:
                emitted += 1
                self._log(sandbox, line, phase)
            else:
                suppressed += 1

        try:
            output = self.pool.exec(handle, command, timeout=timeout, on_line=on_line)
        except TypeError:
            # A pool predating the streaming signature. Still correct, just not live.
            output = self.pool.exec(handle, command, timeout=timeout)
            self._log_output(sandbox, output, phase)
            return output
        if suppressed:
            self._log(sandbox,
                      f"... {suppressed} more lines; full output in files ...", phase)
        return output

    def _log_output(self, sandbox: str, output: str, phase: str) -> None:
        """Emit a command's real output, bounded rather than summarised.

        The previous behaviour kept the last five lines of every command, which
        threw away the installation a build actually performed and, when a
        command failed early, kept five lines of trailing noise instead of the
        error. Long output is elided in the middle: the head shows what the
        command started doing and the tail carries the failure, and the count of
        dropped lines is stated rather than silently omitted.
        """
        lines = [line for line in str(output or "").splitlines() if line.strip()]
        if len(lines) > SANDBOX_LOG_LINES:
            head = SANDBOX_LOG_LINES // 3
            tail = SANDBOX_LOG_LINES - head
            elided = len(lines) - head - tail
            lines = (lines[:head]
                     + [f"... {elided} lines elided ..."]
                     + lines[-tail:])
        for line in lines:
            self._log(sandbox, line, phase)

    def _log(self, sandbox: str, line: str, phase: str) -> None:
        self.emit("artifact", {"kind": "sandbox_log", "sandbox": sandbox,
                               "line": self._redact(line)[:SANDBOX_LOG_LINE_CHARS],
                               "phase": phase})

    def _sandbox_file(
        self,
        sandbox: str,
        content: str,
        revision: int,
        path: str = "adapter.py",
    ) -> None:
        """Persist the adapter source as durable, redacted execution evidence."""
        cleaned = self._redact(content)
        limit = 12_000
        if len(cleaned) > limit:
            cleaned = cleaned[:limit] + f"\n# ... truncated {len(cleaned) - limit} characters"
        self.emit("artifact", {
            "kind": "sandbox_file",
            "sandbox": sandbox,
            "path": path,
            "language": "python",
            "revision": revision,
            "phase": "building" if revision == 1 else "validating",
            "content": cleaned,
        })
