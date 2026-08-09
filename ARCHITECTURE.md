# ProofBench architecture

This is a map of what the code does today. The product boundary it sits inside
is recorded in [ADR-0001: Local product boundary](docs/adr/0001-local-product-boundary.md):
ProofBench is a proprietary, source-visible, solo-operated local pre-release
running as one hardened single-host unit. Interface guarantees are in
[CONTRACTS.md](CONTRACTS.md).

Everything described in the present tense below is implemented. Anything not
built yet appears only under [Future paths](#future-paths), where it is labelled
as a decision, not a feature.

## Shape

```text
Browser (React 18 / Vite, web/)
        |
        | same-origin REST + authenticated SSE
        v
loopback Nginx :8080  --->  FastAPI :8000 (server/)  --->  SQLite WAL + owned volumes
                                    |
                                    +--> engine/docs_intel.py      search and scrape
                                    +--> engine/adapter_gen.py     adapter generation
                                    +--> engine/sandbox_pool.py    one Daytona sandbox per candidate
                                    +--> engine/evaluate.py        deterministic scoring
                                    +--> engine/report_gen.py, pdf_report.py
```

One API replica, one web replica, one durable host. SQLite WAL is the single
transactional store; run artefacts, uploaded datasets, and the sandbox ownership
ledger live on named volumes.

## Components

| Path | Responsibility |
|---|---|
| `web/` | React console and landing page; Nginx image. Holds the bearer in memory only. |
| `server/main.py` | FastAPI application, configuration validation, request telemetry. |
| `server/security.py` | Fail-closed authentication, tenant resolution, redaction. |
| `server/state.py`, `server/storage.py` | Durable transactional state, migrations, artefact volumes. |
| `server/runs.py` | Run admission, claims, quotas, terminal-state persistence. |
| `engine/agent.py` | Orchestrator loop and capability-bound tool dispatch. |
| `engine/tools.py` | Run-scoped capabilities; the only path to credentials. |
| `engine/docs_intel.py`, `engine/scrapers.py` | Candidate discovery through an ordered Scrape.do, Oxylabs, and Bright Data chain, then the bounded direct-fetch fallback. |
| `engine/llm_clients.py` | Capability-based provider resolution, distinct-supervisor identity, and hardened OpenAI-compatible clients. |
| `engine/supervisor.py` | Bounded, one-shot correction of a deterministic violation by a DISTINCT reviewer model. |
| `engine/integration_agent.py` | Settings research assistant gated on one code-generation LLM and one complete search/scrape provider; returns bounded, non-activating connector proposals. |
| `engine/adapter_gen.py` | Generates one adapter per candidate from scraped docs. |
| `engine/sandbox_pool.py` | Disposable sandbox lifecycle and ownership ledger. |
| `engine/evaluate.py` | Deterministic, network-free, LLM-free evaluator. |
| `engine/tool_assessment.py` | Documentation and feasibility assessment path. |
| `engine/builtin_adapters.py` | Server-owned registry of first-party adapters and their exact credential entitlements. |
| `engine/report_gen.py`, `engine/pdf_report.py` | Redacted report and PDF. |

## Request and run flow

1. The browser exchanges a tenant token for an in-memory bearer plus a
   short-lived HttpOnly, SameSite=Strict cookie. The cookie carries read-only
   transports (SSE, report downloads) only; writes require the bearer or an API
   key header.
2. A session is created and scoped to one tenant. Every later lookup applies
   ownership before disclosing that a resource exists.
3. A benchmark specification is built with an explicit `benchmark_type`
   discriminator: `extraction` (labelled dataset, deterministic field scoring)
   or `tool_assessment` (docs and feasibility, no fabricated metrics). A tool
   assessment additionally records `execution_mode` and `assessment_basis`, so a
   `comparison_only` product is scored from documentation without any sandbox
   being provisioned or any execution being implied.
4. Starting a run allocates a new immutable `run_id` and artefact directory.
   A retry never reuses one.
5. Each candidate gets a disposable sandbox, granted only run-scoped
   capabilities. Orchestration, search, scrape, and report credentials are
   permanently forbidden inside candidate sandboxes.
6. `engine/evaluate.py` compares candidate output with the owned
   `ground_truth.csv`. It has no network access and makes no LLM calls. Models
   may explain measured numbers; they never produce or judge them.
7. Results persist with `measured` provenance; `synthetic` is a read-only label
   retained on historical runs and is never written by the current code path.
   A claimed run
   without persisted metrics reports `pending`; a row with metrics but no
   authoritative provenance reports `unverified` and withholds the metrics.
8. Events stream over SSE: append-only, monotonically sequenced, bounded, and
   redacted before the write.

The Settings integration agent is a separate request/response path. It does not
join a benchmark run or receive run capabilities. The server checks its two
mandatory prerequisites and then runs a bounded tool loop on the selected
code-generation LLM: the agent reads its own deployment state, searches and
reads vendor documentation through the configured scraper chain, and applies
provider settings.

Its write tools are exactly the settings the operator can already change in the
Settings UI — a credential, a model, a base URL, the scraper chain order — and
each one is delegated back to the server, which re-validates it on the same code
path as the corresponding endpoint. The agent therefore holds no authority its
caller lacks. It can explain or generate connector code in its response, but it
cannot change the source tree, dependencies, or runtime activation state.

Credential *values* never enter the model's context. A key the operator pastes
into the conversation is lifted out server-side and replaced with an opaque
reference; the agent asks for that reference to be stored under a variable name
and the server resolves it at the write. When no key has been pasted, the agent
names the variable instead and the client renders a field that posts straight to
the credentials endpoint.

## Stable seams

These are the extension points ADR-0001 commits to keeping stable. They exist
because the ownership model is real in the code, not because a second operator
exists today.

- **Tenancy.** Every session, run, event, message, dataset, setting, and
  artefact is owner scoped. Cross-tenant reads return 404.
- **Auth.** Identity resolution is one fail-closed path with two configured
  modes, reported to the browser as `auth_mode`. `local`
  (`PROOFBENCH_INSECURE_DEV=1`) is the default supported solo shape: tokenless,
  loopback only, one deterministic tenant. `authenticated`
  (`PROOFBENCH_API_KEYS`) is required for anything reachable by anyone else and
  keeps the bearer/API-key writes plus read-only cookie transport. The API
  refuses to start with neither configured.
- **Storage.** Clients address resources only by server-issued IDs. Absolute
  filesystem paths never leave the server.
- **Provenance.** Provenance is assigned at persistence time and rendered
  verbatim by the UI and reports.
- **Retention.** Deletion enters a durable tombstone queue before filesystem
  work and stays retryable and observable.
- **Provider.** Credential entitlements are exact and server owned. User input,
  candidate names, URLs, and generated code cannot widen them. Provider
  *selection* is capability based: orchestration, assessment, report writing,
  and adapter generation each resolve to the first configured provider in a
  fixed preference order, so OpenRouter can serve all of them alone. Selection
  never widens an entitlement; every orchestration prefix, `OPENROUTER_`
  included, stays permanently outside sandboxes.
- **Supervision.** A *supervision* capability resolves a DISTINCT `(provider,
  model)` reviewer for the checkpoints that correct a model's own output — intake
  spec recovery, shortlist elimination, and the assessment self-check —
  because a model reviewing its own answer inherits its own bias and laziness.
  `engine/supervisor.py` runs exactly one toolless, temperature-0 correction
  against that identity, hands it the concrete deterministic violation and the
  output contract, and accepts the reply only through the same parser/normalizer/
  validator the primary path uses — no tools, no retries, no supervisor of a
  supervisor. `SUPERVISOR_PROVIDER`/`SUPERVISOR_MODEL` configure it; the same
  provider may supervise only when `SUPERVISOR_MODEL` names a genuinely different
  model. When no distinct identity exists the correction is skipped honestly
  rather than re-asked of the same model, and deterministic scoring never depends
  on it, so single-provider deployments stay fully functional. No model, primary
  or supervisor, is ever placed in deterministic evaluation, result collation,
  credentials, sandbox entitlements, auth, quota, or provenance.
- **Sandbox.** One disposable sandbox per candidate attempt, destroyed on
  success, failure, timeout, or cancellation, with reconciliation that never
  touches unowned resources.

## Data and retention

Uploaded datasets, run artefacts, reports, and the SQLite database live on the
host's named volumes. `PROOFBENCH_RETENTION_DAYS` defaults to `0`, which means
no automatic expiry: an operator's own data is kept until they delete it.
Setting it to a positive number enables expiry of completed tenant data; active
runs are never expired. See [docs/DATA_HANDLING.md](docs/DATA_HANDLING.md).

Benchmark runs send documents to disposable sandboxes and to whichever
third-party providers the operator enables, under those providers' own terms.

## Operational surface

`GET /api/live` is public process liveness. `GET /api/ready` is authenticated
and verifies auth configuration, SQLite, and writable volumes. `GET /api/metrics`
is authenticated and bounded. Containers run non-root with a read-only root
filesystem and no added capabilities. The day-to-day runbook is
[docs/OPERATIONS.md](docs/OPERATIONS.md).

## Future paths

Decisions only. None of this is built, and none of it should be built before the
corresponding stage in ADR-0001 is entered deliberately.

- **Hosted SaaS** would add multi-host components behind the seams above: a
  network database, object storage, an external job queue, and an external
  secret manager. It would also switch the retention default to a finite,
  contractually disclosed horizon.
- **Licensed enterprise / on-premises** would keep the single-host shape and the
  no-expiry retention default, and would add reviewed third-party notices, a
  written licence, and validated handoff procedures. See
  [docs/DISTRIBUTION_CHECKLIST.md](docs/DISTRIBUTION_CHECKLIST.md).

Neither path implies any availability, support, or retention commitment today.
