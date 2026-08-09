# Railway client-trial runbook

This deploys the repository for one invited client's short end-to-end test. It
is not a general-availability production architecture and defines no SLA.

## Create the services

1. Create a Railway project from this GitHub repository.
2. Add Railway PostgreSQL to the project.
3. Add an application service from the repository. `railway.json` selects
   `Dockerfile.railway`; no start command is needed.
4. Add a volume to the application service and mount it at `/app/runtime`.
5. Generate a public domain for the application service.

Keep exactly one application replica. The volume and in-process run worker are
not horizontally shareable.

## Required application variables

Use Railway's reference-variable picker for the database URL. If the database
service is named `Postgres`, the resulting value is normally:

```text
PROOFBENCH_DATABASE_URL=${{Postgres.DATABASE_URL}}
PROOFBENCH_INSECURE_DEV=0
PROOFBENCH_API_KEYS={"client-trial":"<32-or-more-random-characters>"}
PROOFBENCH_COOKIE_SECURE=true
PROOFBENCH_RETENTION_DAYS=7
PROOFBENCH_DEPLOYMENT_ID=railway-client-trial
PROOFBENCH_RECONCILE_SANDBOXES_ON_STARTUP=1
```

Generate the client token in a password manager or with
`python -c "import secrets; print(secrets.token_urlsafe(32))"`. Send it to the
client through a separate secure channel. Never commit it or paste it into a
ticket. Keep `PROOFBENCH_DEPLOYMENT_ID` unchanged across deploys.

Add `DAYTONA_API_KEY` and at least one supported LLM credential. OpenRouter can
serve every LLM capability by itself. Add one search/scrape provider credential
if the client will test discovery or the integration agent. Review the Daytona
target and sandbox memory settings in `.env.example` before a paid run.

Railway injects `PORT` and `RAILWAY_PUBLIC_DOMAIN`; do not set them manually.
The application automatically permits its exact Railway HTTPS origin.

## Deploy and verify

Wait for `/api/deploy-ready` to report `{"status":"ready"}` and for the Railway
deployment to become healthy. Then perform this provider-free check:

1. Open `/app/datasets`; confirm the API-token gate appears.
2. Sign in with the invited-client token.
3. Reload; confirm the UI asks for the token again before write access returns.
4. Generate the sample labelled dataset and navigate through Benchmark, Runs,
   and Settings.
5. Confirm an authenticated `GET /api/ready` reports all checks true.

For the actual end-to-end trial, configure paid providers and run one bounded
benchmark on synthetic sample data first. Confirm live progress/SSE, terminal
state, measured results, and PDF download before accepting client documents.
Provider calls and Daytona sandboxes can incur cost; there is no simulated run
mode.

Railway may terminate or replace a deployment while a long request is open.
The event stream can reconnect to durable events, but plan a quiet deployment
window and do not deploy while a client benchmark is running.

## Backup and rollback

Enable Railway PostgreSQL backups appropriate to the trial and snapshot/export
the `/app/runtime` volume separately. Database backups do not contain uploaded
files or run artifacts. Before an image/schema change, stop new runs, verify the
latest database backup and volume copy, then deploy. Rollback means restoring a
matched database-and-volume set and the prior image revision.

At trial end, stop new runs, export anything the client is entitled to receive,
delete their sessions/datasets, verify retention tombstones complete, and then
remove the Railway services/backups according to the disclosed trial terms.
