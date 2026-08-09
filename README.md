# ProofBench

ProofBench evaluates software tools from their documented integration paths and
produces evidence-backed, provenance-labelled results. Extraction benchmarks
run generated adapters in disposable Daytona sandboxes and score outputs with a
deterministic evaluator. Tool assessments evaluate documentation and validated
integration feasibility without inventing extraction scores.

A tool assessment states how each candidate was judged. A runnable, safe,
credential-free artefact is `sandbox_verifiable` and may be exercised in
Daytona. A cloud or SaaS product, anything needing a paid plan or a credential
ProofBench does not hold, and anything whose documented operations are
destructive is `comparison_only`: it is scored 0-100 from bounded documentation
evidence, no sandbox is provisioned, and nothing claims it was executed. When no
assessment can be produced at all, scores are withheld and rendered as
unavailable rather than persisted as zeros.

## Production architecture

```text
Browser (React/Vite)
        |
        | same-origin REST + authenticated SSE
        v
local: TLS ingress -> Nginx :8080 -> FastAPI :8000 -> SQLite WAL + owned volumes
trial: Railway HTTPS -> FastAPI + built React -> PostgreSQL + one runtime volume
                         |
                         +--> docs/search providers
                         +--> trusted adapter generation
                         +--> disposable Daytona sandbox per candidate
                         +--> deterministic evaluator + redacted report/PDF
```

Authentication is fail closed, resources are tenant scoped, attempts have
immutable run IDs, datasets use server-issued IDs, and persisted results declare
their provenance. ProofBench is real-only: every new run persists `measured`,
and `synthetic` survives solely as a read-only label on historical runs. Both
supported shapes use one API replica. See [CONTRACTS.md](CONTRACTS.md) and
[docs/OPERATIONS.md](docs/OPERATIONS.md) for the supported boundary.

The Railway shape is a narrow, single-client trial, not general availability or
an SLA-backed production service. See [License and project status](#license-and-project-status).

## Requirements

- Python 3.12
- Node.js 22.12 or newer
- Docker Desktop or Docker Engine with Compose v2 for the production stack
- Provider credentials for the integrations you enable

If you configure Daytona, set `DAYTONA_TARGET` to a region your organization is
entitled to and declare your tier's concurrent-memory budget before the first
run. Getting either wrong makes every benchmark fail in ways that read like
something else — see [Sandbox capacity and cleanup](#sandbox-capacity-and-cleanup).

## Railway client-trial quickstart

The repository includes `railway.json` and `Dockerfile.railway`. Add a Railway
PostgreSQL service, connect its `DATABASE_URL` to the application as
`PROOFBENCH_DATABASE_URL`, mount one application volume at `/app/runtime`, use
authenticated mode, and generate a public domain. Do not add a custom start
command and keep the application at one replica.

The exact variables, provider checklist, backup procedure, and end-to-end smoke
are in [docs/RAILWAY.md](docs/RAILWAY.md). The public healthcheck is
`/api/deploy-ready`; authenticated `/api/ready` includes detailed database and
filesystem checks.

## Local development

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-dev.txt
Copy-Item .env.example .env
Set-Location web
npm ci
Set-Location ..
```

`.env.example` already ships the tokenless local profile, so `.env` needs no
access-control edits for a solo machine:

```dotenv
PROOFBENCH_INSECURE_DEV=1
PROOFBENCH_DEV_TENANT=local-dev
PROOFBENCH_API_KEYS=
```

Then run two terminals from the repository root:

```powershell
.venv\Scripts\python.exe -m uvicorn server.main:app --port 8000
```

```powershell
Set-Location web
npm run dev
```

Open `http://localhost:5173`. The console enters without a sign-in screen. Never
enable the tokenless local mode on a shared, staged, or externally reachable
host: it authenticates nothing.

## Compose quickstart

Compose is the portable path and the one to prefer when moving between
machines. Both images pin their base by digest and install from a hash-locked
`requirements.txt`, so the stack that runs on one host runs identically on
another; nothing depends on a local Python, Node, or virtualenv.

Pick one of two deployment profiles. The API refuses to start with neither.

### Moving between macOS, Windows, and Linux

The commands below are identical on all three (PowerShell, `cmd`, and any POSIX
shell), because everything runs inside the containers. What travels with you is
`.env`, which is deliberately gitignored: clone the repository, copy
`.env.example` to `.env`, paste your provider credentials back in, and start the
stack. Runs and datasets live in the `proofbench-runs` and `proofbench-data`
named volumes and stay on the machine that created them.

Two portability rules the code already follows, worth knowing because breaking
either is what makes a checkout "work on one machine only":

- **Never hardcode an interpreter path.** A helper that looked for the Windows
  venv layout (`.venv\Scripts\python.exe`) and fell back to a bare `python`
  failed on every POSIX host and in the container, where neither exists; it now
  resolves the running interpreter. Paths are built with `os.path.join`, never
  by concatenating separators.
- **Keep host tooling out of the runtime.** The API image carries its own Python
  3.12 and dependencies. Installing `uv`, a virtualenv, or Node on the host is a
  convenience for editing the source, never a requirement for running it.

Docker Desktop must be running before `docker compose` will do anything; on
Windows that is the single most common cause of a stack that "stopped working".

### Local (default, tokenless, loopback only)

This is the supported shape for a single operator on one machine.

1. Copy `.env.example` to `.env`. It already ships the local profile:
   `PROOFBENCH_INSECURE_DEV=1`, `PROOFBENCH_DEV_TENANT=local-dev`, and an empty
   `PROOFBENCH_API_KEYS`.
2. Set the provider credentials needed by the integrations you enable.
3. Start the pinned, non-root, read-only stack:

```powershell
docker compose build --pull
docker compose up -d --wait
```

Open `http://127.0.0.1:8080/`. There is no sign-in screen and no password:
every request resolves to the single `local-dev` tenant, and the header shows
`Local mode` instead of a sign-out control.

> **Loopback only.** The local profile has no authentication whatsoever.
> Anything that can reach the listener has full read and write access to your
> benchmark data. Keep `PROOFBENCH_BIND_ADDRESS=127.0.0.1`. Do not port-forward
> it, publish it on a LAN or `0.0.0.0` address, or put it behind an ingress.
> Exposing ProofBench to anyone but you means opting into the authenticated
> profile below.

### Authenticated (any deployment reachable by anyone else)

1. Copy `.env.example` to `.env`.
2. Set `PROOFBENCH_INSECURE_DEV=0` and set `PROOFBENCH_API_KEYS` to a
   tenant-to-random-token JSON map. Each token must contain at least 32
   characters.
3. Set the provider credentials needed by your deployment.
4. Terminate TLS at a trusted ingress that strips incoming forwarding headers,
   preserves the external Host, and sets `X-Forwarded-Proto`; set
   `PROOFBENCH_COOKIE_SECURE=true`.
5. Start the same stack:

```powershell
docker compose build --pull
docker compose up -d --wait
```

Compose exposes Nginx on `127.0.0.1:8080` by default. Keep that loopback bind
for a same-host TLS proxy. No images are published anywhere: pushing a `v*` tag
does nothing, and the manual release workflow is deliberately blocked (see
[Verification](#verification)). Build locally with `docker compose build --pull`.
`PROOFBENCH_API_IMAGE` and `PROOFBENCH_WEB_IMAGE` exist for a future in which
published digests exist; leave them unset and Compose builds from source.

In the authenticated profile, open `https://your-host/` and enter the tenant
token in the sign-in screen. The browser keeps the bearer only in memory; the
API issues a short-lived HttpOnly cookie for read-only SSE and report requests.

Operational probes:

```powershell
# Local profile (no credential):
curl.exe http://127.0.0.1:8080/api/live
curl.exe http://127.0.0.1:8080/api/ready

# Authenticated profile:
curl.exe https://your-host/api/live
curl.exe -H "Authorization: Bearer YOUR_TOKEN" https://your-host/api/ready
```

## Verification

```powershell
.venv\Scripts\python.exe -m pytest -q --basetemp=.pytest_local_tmp
.venv\Scripts\python.exe integration_test.py
.venv\Scripts\python.exe -m pip check
.venv\Scripts\python.exe -m pip_audit -r requirements.txt --require-hashes

Set-Location web
npm run lint:a11y
npm test
npm run build
npm audit --audit-level=high
```

None of the above contacts a provider. The Playwright suite (`npm run test:e2e`)
is likewise provider-free: it asserts the console exposes no demo execution
control and that an explicit `mode: "demo"` is rejected at the schema boundary
without allocating a session or a run.

One test is deliberately excluded from that guarantee and is skipped unless you
opt in:

```powershell
$env:PROOFBENCH_RUN_LIVE_SMOKE = "1"
# Only against an authenticated deployment; omit for the local profile.
# $env:PROOFBENCH_E2E_TOKEN = "<tenant token>"
npm run test:e2e:live
```

This performs one real run — OpenAI intake plus Daytona execution — and **incurs
provider cost**. It is held to one sample labelled dataset, one short objective,
and the first-party local candidates (`tesseract`, `easyocr`) so no paid hosted
candidate inference is billed, under a strict wall-clock budget
(`PROOFBENCH_LIVE_SMOKE_TIMEOUT_MS`, default 15 minutes). It asserts the
completed run persists immutable `measured` provenance with metrics, and that
the report PDF downloads.

CI repeats these checks on Windows and Linux, generates Python and npm SBOMs,
runs CodeQL, builds both container images, scans them for high/critical issues,
and starts/probes the Compose stack.

Nothing is published. `.github/workflows/release.yml` runs only on manual
`workflow_dispatch` — pushing a `v*` tag does not build or publish anything —
and it is intentionally blocked today: it refuses to run until a human-reviewed
`THIRD_PARTY_NOTICES.md` exists and the operator acknowledges
[docs/DISTRIBUTION_CHECKLIST.md](docs/DISTRIBUTION_CHECKLIST.md). The notice is
absent, so the workflow is expected to fail. It is kept wired up (SBOM,
maximum provenance attestation, pinned actions, immutable digests) so the
procedure stays testable. Local builds are unaffected.

## Main product flow

1. Sign in and create a benchmark session.
2. Attach labelled data one of four ways: pick a dataset you already own,
   generate the sample one, upload images plus a matching `ground_truth.csv`, or
   describe what you want to test and let ProofBench design a dataset for it.
   Every option is previewed before a run uses it - kind, field schema, sample
   documents, and the first ground-truth rows.
3. Review the explicit extraction or tool-assessment specification.
4. Start a run. Every run executes for real; there is no demo or simulated mode.
   Each retry gets a new immutable run ID.
5. Follow the redacted trace stream and sandbox lifecycle.
6. Review provenance, deterministic metrics where applicable, citations, and the
   downloadable report. New runs persist `measured`; `synthetic` appears only on
   read-only historical runs written before ProofBench became real-only.

## Extraction schemas

A benchmark declares the columns it is scored on, and each column declares a
type. ProofBench began with the four invoice fields pinned in the API schema and
normalization dispatching on a field's *name* - `date` parsed as a date, `total`
as an amount - which meant a receipts, purchase-order, or shipping-label
benchmark could not be expressed at all, let alone scored.

Comparison now dispatches on the declared type (`engine/fields.py`), so a
benchmark names whatever columns its documents actually have:

```json
"fields": [
  {"name": "po_ref",      "type": "text"},
  {"name": "issued_on",   "type": "date"},
  {"name": "amount_due",  "type": "currency"},
  {"name": "line_count",  "type": "number"}
]
```

`text` compares case-folded and whitespace-collapsed; `date` as a calendar date,
so `03/04/2026` and `2026-04-03` agree; `currency` in minor units, so `$1,234.50`
and `1234.5` agree; `number` as a plain value. A bare list of names is still
accepted and carries the typing those names always had, so existing specs,
datasets, and stored runs mean exactly what they meant before.

The schema travels into each sandbox as `pb_schema.json`, and every first-party
adapter reads it, so one adapter serves any schema rather than hardcoding
columns. Generated adapters are told the requested fields in their prompt.

## Designed datasets

When no labelled data exists, ProofBench can build some. The orchestration model
proposes a document kind, a typed schema, and the ground-truth rows; a
deterministic Pillow renderer (`engine/dataset_gen.py`) then draws one document
image per row across rotating layout templates.

The model authors *content only*. Every pixel is drawn by fixed code from
validated rows, so the ground truth is exact by construction rather than by a
second model reading the images back - there is no labelling error to inherit.
Field names, types, row counts, and value sizes are all validated before
anything is rendered, and rendering is seeded from the proposal, so the same
proposal produces byte-identical documents on any machine.

Generated datasets are ordinary dataset directories (`images/` plus
`ground_truth.csv`) with `schema.json` and `manifest.json` alongside, and the
console previews them before a run uses one.

## Sandbox capacity and cleanup

Every candidate runs in its own disposable Daytona sandbox. Two provider facts
govern whether a run executes at all, and neither of them announces itself
clearly when it is wrong.

**Your region must be entitled.** `DAYTONA_TARGET` has to name a region your
organization can use for container-class sandboxes. A wrong region does not say
so plainly: creating from an image reports `Declarative builds are not available
to your organization in region <x>`, which reads like a build-feature problem.
The honest error only appears when creating from a snapshot: `Region <x> is not
available to the organization for class container`. If sandbox creation fails
everywhere, check the region before anything else.

**Concurrency is metered in memory, not in sandboxes — and stopped sandboxes
still count.** Providers cap total concurrent sandbox memory per account. A
sandbox that has been stopped but not deleted keeps consuming that budget, so
leaked sandboxes silently starve every later run. Declare the budget and the
pool will never ask for more than it allows:

```dotenv
# Total concurrent sandbox memory your tier permits, in GiB. 0 disables the cap.
PROOFBENCH_SANDBOX_MEMORY_BUDGET_GIB=10
# Per-sandbox shape. 4 GiB is the supported baseline (EasyOCR needs it).
PROOFBENCH_SANDBOX_MEMORY_GIB=4
```

With those values the pool runs at most two sandboxes at once. Additional
candidates wait for a peer to finish rather than asking the provider for memory
it will refuse. Leaving the budget unset restores the previous behaviour: the
pool asks for as many as the pipeline is wide, and the provider rejects the
excess.

### Sandboxes leak when a run is killed

Cleanup runs when a run ends. A run that never ends — because the API process
was restarted, killed, or crashed mid-flight — leaves its sandboxes alive on the
provider, holding memory budget indefinitely. This is the failure that looks
like something else: later runs appear to hang, because each one is retrying
`Total memory limit exceeded` against a wall that will never clear.

Two safeguards exist, and one operator habit matters more than both:

- The orphan reconciler deletes sandboxes recorded in this deployment's
  ownership ledger that no active run claims. It runs at API startup and every
  ten minutes thereafter, so a leak self-heals within minutes.
  `PROOFBENCH_RECONCILE_SANDBOXES_ON_STARTUP=0` disables it; keep it on.
  Reconciliation is scoped to `PROOFBENCH_DEPLOYMENT_ID`, so keep that stable
  across restarts or the ledger loses track of what it owns.
- Sandbox creation retries a transient `limit exceeded`, which covers the normal
  case of a previous run's sandboxes still tearing down.

**Stop a running benchmark before restarting the API.** Use the console's Stop
control or `POST /api/sessions/{id}/stop`. A clean stop releases sandboxes
immediately; a killed process defers that work to the reconciler.

To audit or clear the account by hand:

```python
from daytona import Daytona

client = Daytona()  # reads DAYTONA_* from the environment
for sandbox in client.list():
    print(sandbox.id, sandbox.state, getattr(sandbox, "memory", "?"))
    # client.delete(sandbox)  # uncomment to remove one
```

Anything not `ARCHIVED` is consuming budget, whatever its state says.

## Repository layout

| Path | Purpose |
|---|---|
| `engine/` | Orchestrator, capability-bound tools, sandbox lifecycle, evaluator, report generation |
| `engine/fields.py` | The typed extraction schema a benchmark is scored against |
| `engine/dataset_gen.py` | AI-proposed, deterministically rendered labelled datasets |
| `engine/snapshots.py` | Prebuilt sandbox images, including the shared base every candidate starts from |
| `server/` | Authenticated FastAPI service and durable state |
| `web/` | React console and Nginx production image |
| `docs/` | Operations and data-handling requirements |
| `.github/` | CI, CodeQL, and dependency update policy |
| `requirements*.in/txt` | Direct inputs and hash-locked Python environments |

Runtime data in `runs/` and `data/` is ignored by Git. Provider credentials must
come from a deployment secret manager or environment and must never be committed.

## Further documentation

- [ARCHITECTURE.md](ARCHITECTURE.md) — component boundaries and data flow
- [docs/adr/0001-local-product-boundary.md](docs/adr/0001-local-product-boundary.md) — ADR-0001, the local single-operator product boundary
- [docs/adr/0002-railway-client-trial.md](docs/adr/0002-railway-client-trial.md) — ADR-0002, the one-client hosted-trial boundary
- [docs/RAILWAY.md](docs/RAILWAY.md) — Railway deployment and verification runbook
- [docs/DISTRIBUTION_CHECKLIST.md](docs/DISTRIBUTION_CHECKLIST.md) — what must be done before any distribution or hosted launch

## Security and data handling

See [SECURITY.md](SECURITY.md) for vulnerability reporting and
[docs/DATA_HANDLING.md](docs/DATA_HANDLING.md) for retention, deletion, and the
privacy prerequisites that a future third-party-facing deployment would have to
satisfy.

## Free scraping without any provider key

The paid scraping chain has a zero-key fallback: **SearXNG** finds candidate
pages and **Crawl4AI** reads them. They are separate third-party services, not
part of this app, so starting the API does not start them — bring them up
yourself:

```
docker compose -f docker-compose.scrapers.yml up -d
```

ProofBench probes `localhost:8080` and `localhost:11235` and picks the pair up
automatically once they answer; Settings shows a live running indicator for
each. Stop them with `docker compose -f docker-compose.scrapers.yml down`.

Reaching a local service over plain HTTP is exactly what the outbound URL policy
forbids, so this path is enabled only when `PROOFBENCH_INSECURE_DEV=1` and only
for loopback or private addresses. It is a local-development convenience, not a
deployment feature.

## Hackathon integrations

ProofBench integrates several third-party services used in the hackathon build:
Daytona for disposable sandboxes; Scrape.do, Oxylabs, and Bright Data for the
ordered search and documentation-scraping chain, with self-hosted SearXNG and
Crawl4AI as a free fallback; Doubleword for adapter
generation and batch inference; OpenRouter as an OpenAI-compatible provider for
orchestration, assessment, and reports; and Kimi and Nosana as configured model
endpoints. Each is reached through its own credentials, which you supply.

Third-party names and trademarks appear only to identify these integrations and
remain the property of their respective owners. Their use does not imply any
current sponsorship, endorsement, partnership, or affiliation.

## License and project status

ProofBench is proprietary software. The copyright holder is the individual
developer who wrote it; there is no company behind it. See [LICENSE](LICENSE):
all rights are reserved, and use, copying, modification, or distribution
requires the copyright holder's prior written permission. Third-party
dependencies are not covered by that file and remain under their own licenses.

ProofBench may be run locally or as the one-client Railway trial described in
[ADR-0002](docs/adr/0002-railway-client-trial.md). The trial is a hosted SaaS
test operated by the copyright holder, but it is not a public signup service,
software distribution, availability commitment, or support commitment.

A broader commercial launch still requires written customer terms, an
appropriate privacy notice, a security contact, reviewed provider disclosures,
and the multi-host work described in [CONTRACTS.md](CONTRACTS.md) section 8.
