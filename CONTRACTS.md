# ProofBench technical interfaces and invariants (v2)

This document defines the internal technical interfaces and safety invariants of
the hardened local build. Git history preserves the hackathon v1 document.

"Contract" here means a constraint binding on the code, not an agreement with
any person. These invariants are binding regardless of who runs the software,
and no change may weaken them. They are not a service commitment: there is no
public ProofBench instance, and nothing here offers availability, support, or
retention to anyone.

Scope: the hardened single-host deployment in this repository, which today is
run locally by the copyright holder. See
[docs/adr/0001-local-product-boundary.md](docs/adr/0001-local-product-boundary.md)
for the product boundary and [ARCHITECTURE.md](ARCHITECTURE.md) for the current
map. A future hosted SaaS or a licensee's adaptation would have to satisfy these
invariants and add the multi-host requirements in section 8, plus the
third-party-facing obligations in
[docs/DATA_HANDLING.md](docs/DATA_HANDLING.md). ProofBench is proprietary; see
[LICENSE](LICENSE).

## 1. Identity and tenancy

- A deployment runs in exactly one of two modes, and refuses to start in
  neither. `GET /api/auth/session` reports which one in `auth_mode`.
  - `local`: `PROOFBENCH_INSECURE_DEV=1` is the explicit, loopback-only
    tokenless bypass. Every request resolves to the single deterministic tenant
    `PROOFBENCH_DEV_TENANT` (default `local-dev`) with no bearer, API key, or
    cookie. `GET /api/auth/session` returns
    `{"auth_mode": "local", "cookie_authenticated": true, "write_authenticated": true}`
    so the console enters without a credential. This mode authenticates nothing
    and must not be reachable beyond `127.0.0.1`.
  - `authenticated`: `PROOFBENCH_API_KEYS`, a JSON map of tenant IDs to random
    tokens of at least 32 characters. Required for any deployment reachable by
    anyone but the operator. `GET /api/auth/session` returns
    `auth_mode: "authenticated"` with the two flags resolved fail closed.
- In `authenticated` mode, state-changing API requests require
  `Authorization: Bearer` or `X-API-Key`.
  The HttpOnly, SameSite=Strict `/api` cookie is limited to read-only browser
  transports such as SSE and report downloads. The sole cookie-authenticated
  write exception is idempotent logout, which also requires an exact same-origin
  `Origin` check and can only clear the auth cookie.
- All sessions, runs, events, messages, datasets, settings, and artifacts are
  owner scoped. Cross-tenant access returns 404 where resource existence would
  otherwise leak.

## 2. Resource identity

- A `session_id` identifies a conversation and its run history.
- Every benchmark attempt receives a new immutable `run_id` and artifact
  directory. Results and reports are addressed only by `run_id`.
- A `dataset_id` is server issued and tenant owned. API responses never expose
  absolute filesystem paths, and client input cannot select a host path.
- Session summaries include `created_at`, `updated_at`, and `latest_run_id`.
  `updated_at` is the durable last-activity timestamp clients use when resuming
  work. Full session responses also include ordered `run_history` entries.

## 3. Core HTTP API

All JSON request bodies are strictly validated and bounded.

```text
GET    /api/auth/session                 auth_mode + cookie/write auth state
POST   /api/auth/session
DELETE /api/auth/session
GET    /api/live                         public process liveness
GET    /api/ready                        authenticated storage readiness
GET    /api/metrics                      authenticated bounded operations summary
POST   /api/chat                         create/continue a session
GET    /api/sessions
POST   /api/sessions
GET    /api/sessions/{session_id}
DELETE /api/sessions/{session_id}
POST   /api/sessions/{session_id}/run    -> {session_id, run_id, status}
POST   /api/sessions/{session_id}/stop
GET    /api/sessions/{session_id}/events SSE
POST   /api/datasets
GET    /api/datasets
DELETE /api/datasets/{dataset_id}
GET    /api/runs/{run_id}/results
GET    /api/runs/{run_id}/report.pdf
GET    /api/settings/provider-keys       secret-free status + write policy
POST   /api/settings/provider-keys       insecure-dev dual opt-in only
DELETE /api/settings/provider-keys/{env} insecure-dev dual opt-in only
GET    /api/settings/scrapers            ordered provider chain + readiness
PUT    /api/settings/scrapers            persist tenant-scoped provider order
GET    /api/brand                        cached marks for owned candidate names
```

Quota rejection uses HTTP 429 and `Retry-After`. A referenced, active, or
synthetic dataset cannot be deleted. A rejected operation does not partially
mutate session state.

## 4. Event stream

Events are append-only, monotonically sequenced, bounded, persisted in SQLite,
and visible across supported worker processes. A terminal event belongs to a
specific operation/run and cannot terminate or overwrite a newer one.

```text
event: delta      data: {"text":"..."}
event: artifact   data: {"kind":"spec|trace|sandbox_log|results|report", ...}
event: state      data: {"phase":"...","candidates":{...}}
event: error      data: {"message":"..."}
event: done       data: {}
```

Persisted and emitted data is redacted before the write. Events never contain
bearer tokens, provider credentials, private host paths, or unbounded strings.

## 5. Benchmark specifications and provenance

Specifications use an explicit discriminator:

- `benchmark_type: "extraction"` selects a labelled dataset and deterministic
  field evaluation.
- `benchmark_type: "tool_assessment"` evaluates documentation and integration
  feasibility without fabricating extraction metrics.

A `tool_assessment` row declares how it was judged, and the two fields are
independent of its score:

- `execution_mode` is `sandbox_verifiable` for a runnable, safe artefact that
  needs no credentials, or `comparison_only` for a cloud/SaaS product, anything
  requiring a paid plan or a credential ProofBench does not hold, and anything
  whose documented operations are destructive or otherwise unsafe to invoke. A
  `comparison_only` candidate never causes a sandbox to be provisioned and
  carries no build commands or verification code.
- `assessment_basis` is `sandbox_execution` only when a sandbox genuinely ran,
  `documentation_evidence` when the rating rests on documentation alone, and
  `unavailable` when no assessment was produced.

Suitability is a 0-100 documentation-evidence score on every path. A
`comparison_only` candidate receives a legitimate score and is never penalised
for being unrunnable; sandbox execution adjusts a score only when execution
actually happened. A failed scrape, a provider outage, or an unparseable
response yields `assessment_basis: "unavailable"` with every score withheld as
`null`. Zeros are never written for work that was not performed, and the UI,
report, and PDF render withheld scores as unavailable.

Every successful result artifact declares `provenance`:

- `measured`: a real benchmark execution. This is the only value the write path
  can produce. ProofBench is real-only: `mode` accepts `"real"` alone, so an
  explicit `"demo"` on `POST /api/chat` or `POST /api/sessions/{id}/run` fails
  schema validation with `422` before any session or run is allocated.
- `synthetic`: read-only historical data. Runs persisted before ProofBench
  became real-only keep this label; it is never written again and never
  rewritten.

A sample labelled dataset may contain synthetic input images, but the metrics
measured against its ground truth are produced by genuine execution and are
therefore `measured`.

While a claimed run has not persisted metrics, the result endpoint reports
`pending`. A legacy or corrupt row with metrics but no authoritative database
provenance reports `unverified` and withholds metrics. Neither state is valid
benchmark evidence.

Failures do not persist plausible-looking metrics. UI and reports use the
backend provenance verbatim and render missing values as unavailable.

## 5a. Provider capabilities and documentation retrieval

LLM provider selection is capability based, not vendor based. Each capability
lists the providers that can serve it in preference order and resolves to the
first one configured: orchestration and report writing use Moonshot, then
OpenAI, then OpenRouter; documentation assessment uses Doubleword, then
OpenRouter, then OpenAI, then DeepSeek; adapter generation uses DeepSeek, then
OpenRouter. `OPENROUTER_API_KEY` alone therefore satisfies every capability.
`GET /api/providers` reports the resolved provider per capability from
configuration only and issues no provider request. A run is blocked only when an
essential capability has no configured provider.

Documentation retrieval uses the tenant's ordered Scrape.do, Oxylabs, and
Bright Data chain, skipping providers without the required capability
credentials and falling through on errors or empty search results. The stored
order cannot remove a provider from the fallback chain. If every configured
scraper fails, ProofBench uses a direct fetch that is strictly bounded: public
HTTPS only, every redirect hop revalidated against the same outbound URL policy
with a freshly pinned client, no process proxies, no private, loopback,
link-local, or metadata addresses, and hard limits on redirect count, elapsed
time, response bytes, permitted content types, and returned text length.
Provider error text is never echoed to the caller.

## 6. Deterministic evaluator

`engine/evaluate.py` is network-free and contains no LLM calls. It compares
candidate JSONL output with an owned `ground_truth.csv` and returns exact
accuracy, field F1, CER, latency, failure rate, cost, setup complexity, and
document count. Candidate/document identities, row counts, field lengths,
result bytes, and edit-distance work are bounded before evaluation.

For extraction, the engine alone invokes each registered `Candidate` adapter
over every authorized image and writes result records. A model may assist with
documentation and adapter generation, but it cannot supply a dataset runner or
append result records through a tool call. The supported scored field schema is
exactly `invoice_number`, `date`, `vendor`, and `total`, in that order.

## 7. Sandbox and credential boundary

- Every candidate attempt uses a disposable sandbox which is destroyed on
  success, failure, timeout, or cancellation.
- Tools operate through run-scoped capabilities: registered candidate IDs,
  document IDs, result path, ground-truth path, and trusted adapter identity.
- Credential entitlements are exact and server owned. User-controlled names,
  URLs, prompts, or generated code cannot grant credentials.
- `DAYTONA`, orchestration, search/scrape, and report-writer credentials are
  permanently forbidden inside candidate sandboxes. The deny prefixes are
  `BRIGHTDATA_`, `DAYTONA_`, `DEEPSEEK_`, `DOUBLEWORD_`, `KIMI_`, `MOONSHOT_`,
  `OPENAI_`, `OPENROUTER_`, `ORCHESTRATOR_`, `OXYLABS_`, and `SCRAPEDO_`. The
  only exceptions are the exact names a first-party adapter genuinely needs,
  enumerated server side in `engine/builtin_adapters.py`. Generated generic
  adapters and documentation verification code are entitled to nothing at all.
- The durable sandbox ledger records ownership before use and removes it only
  after confirmed deletion. Reconciliation is deployment scoped, leader
  coordinated, age/lease aware, and never lists or deletes unowned resources.

## 8. Persistence and lifecycle

- SQLite WAL is the supported single-host transactional store. Schema upgrades
  run through ordered migrations and require a tested backup/rollback point.
- Admission, ownership, run claims, quotas, dataset references, and deletion
  tombstones are transactional.
- `PROOFBENCH_RETENTION_DAYS` defaults to `0`, meaning no automatic expiry, so
  a local operator's own data is never deleted on a horizon they did not choose.
  A positive value expires completed tenant data after that many days. Active
  resources are never expired. Filesystem deletion failures remain durably
  queued and observable until retried.
- The supported Compose deployment uses one API replica. Scaling across hosts
  requires a network database, object storage, external queue, and secret
  manager while preserving these contracts.

## 9. Deployment contract

- The web app uses same-origin `/api` through Nginx. Production TLS termination
  sets `PROOFBENCH_COOKIE_SECURE=true`.
- Containers run non-root with a read-only root filesystem, no added Linux
  capabilities, and persistent volumes only for runtime state.
- Liveness, authenticated readiness, unit/integration tests, accessibility
  checks, locked-dependency audits, SBOM generation, container builds, image
  scans, and Compose smoke tests are release gates.

Any intentional change to these interfaces or invariants must update this file,
tests, operations documentation, and the relevant client in the same change.
