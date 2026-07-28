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
TLS ingress ---> loopback Nginx :8080 ---> FastAPI :8000 ---> SQLite WAL + owned artifact volumes
                         |
                         +--> docs/search providers
                         +--> trusted adapter generation
                         +--> disposable Daytona sandbox per candidate
                         +--> deterministic evaluator + redacted report/PDF
```

Authentication is fail closed, resources are tenant scoped, attempts have
immutable run IDs, datasets use server-issued IDs, and persisted results declare
their provenance. ProofBench is real-only: every new run persists `measured`,
and `synthetic` survives solely as a read-only label on historical runs. The
included Compose deployment supports
one API replica on one durable host. See [CONTRACTS.md](CONTRACTS.md) and
[docs/OPERATIONS.md](docs/OPERATIONS.md) for the supported boundary.

This hardened single-host stack is what "production" means throughout these
docs: the controls are built and tested, but they are exercised today only in
local, single-operator use. See [License and project status](#license-and-project-status).

## Requirements

- Python 3.12
- Node.js 22.12 or newer
- Docker Desktop or Docker Engine with Compose v2 for the production stack
- Provider credentials for the integrations you enable

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

Pick one of two deployment profiles. The API refuses to start with neither.

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

Open `http://127.0.0.1:8080/`. There is no sign-in screen and no API token:
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
and it is intentionally blocked today: it refuses to run until the LICENSE
legal-name placeholder is resolved, a human-reviewed `THIRD_PARTY_NOTICES.md`
exists, and the operator acknowledges
[docs/DISTRIBUTION_CHECKLIST.md](docs/DISTRIBUTION_CHECKLIST.md). None of those
is satisfied, so the workflow is expected to fail. It is kept wired up (SBOM,
maximum provenance attestation, pinned actions, immutable digests) so the
procedure stays testable. Local builds are unaffected.

## Main product flow

1. Sign in and create a benchmark session.
2. Select an existing owned dataset, generate the sample labelled dataset, or
   upload images plus a matching `ground_truth.csv`. The sample dataset's images
   are synthetic, but they carry known ground truth, so a real run measured
   against them produces genuine metrics.
3. Review the explicit extraction or tool-assessment specification.
4. Start a run. Every run executes for real; there is no demo or simulated mode.
   Each retry gets a new immutable run ID.
5. Follow the redacted trace stream and sandbox lifecycle.
6. Review provenance, deterministic metrics where applicable, citations, and the
   downloadable report. New runs persist `measured`; `synthetic` appears only on
   read-only historical runs written before ProofBench became real-only.

## Repository layout

| Path | Purpose |
|---|---|
| `engine/` | Orchestrator, capability-bound tools, sandbox lifecycle, evaluator, report generation |
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
- [docs/DISTRIBUTION_CHECKLIST.md](docs/DISTRIBUTION_CHECKLIST.md) — what must be done before any distribution or hosted launch

## Security and data handling

See [SECURITY.md](SECURITY.md) for vulnerability reporting and
[docs/DATA_HANDLING.md](docs/DATA_HANDLING.md) for retention, deletion, and the
privacy prerequisites that a future third-party-facing deployment would have to
satisfy.

## Hackathon integrations

ProofBench integrates several third-party services used in the hackathon build:
Daytona for disposable sandboxes; Scrape.do, Oxylabs, and Bright Data for the
ordered search and documentation-scraping chain; Doubleword for adapter
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

There is no public ProofBench service, no hosted instance, and no domain. The
only supported deployment today is the hardened stack in this repository, run
locally or on a single trusted host by the copyright holder. Nothing here is an
offer of a service, and no availability, support, or retention commitment is
made to anyone.

Selling ProofBench as a hosted SaaS, or licensing it to a company to adapt,
remains open. Either would first require, at minimum: the holder's legal name
recorded in [LICENSE](LICENSE), a written contract, a published privacy notice
and terms, a named security contact, and the multi-host work described in
[CONTRACTS.md](CONTRACTS.md) section 8. None of that exists yet, so no
third-party-facing claims should be made about this software.
