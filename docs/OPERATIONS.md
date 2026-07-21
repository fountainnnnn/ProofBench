# Operations

This document has two parts, and they are not the same kind of thing.

**Part 1, the current local runbook**, is the operating procedure for the
hardened single-host stack in this repository as the copyright holder runs it
today. It describes real steps against a real deployment of one.

**Part 2, [future hosted and enterprise commitments](#part-2-future-hosted-and-enterprise-commitments)**,
collects the RPO, RTO, incident, and disclosure obligations that would apply if
ProofBench were ever operated for someone else. None of it is in force. None of
it is offered to anyone. It is recorded here so the work is scoped, not so it
can be cited as a commitment.

No availability, response, or support commitment is offered for ProofBench
today. See
[docs/adr/0001-local-product-boundary.md](adr/0001-local-product-boundary.md).
Serving other parties would additionally require the items in
[DATA_HANDLING.md](DATA_HANDLING.md#future-launch-checklist-not-yet-satisfied)
and [DISTRIBUTION_CHECKLIST.md](DISTRIBUTION_CHECKLIST.md).

## Part 1: current local runbook

### Supported deployment boundary

The included Compose stack runs one non-root FastAPI replica and one non-root
Nginx replica on a single durable host. SQLite WAL, run artifacts, uploaded
datasets, and the sandbox ownership ledger live on named volumes. Do not mount
SQLite on NFS or deploy multiple API hosts. Horizontal scaling requires a
network database, object storage, an external job queue, and a secret manager.

Terminate HTTPS at a same-host or private-network ingress. Compose binds Nginx
to `127.0.0.1` by default so plaintext port 8080 is not remotely reachable.
The ingress must discard client-supplied forwarding headers, preserve the
external `Host` (including a non-default port), and set `X-Forwarded-Proto` to
the external scheme. Set `PROOFBENCH_COOKIE_SECURE=true`; the internal
Nginx-to-API hop is HTTP by design. Restrict `PROOFBENCH_ALLOWED_ORIGINS` to
exact production origins.

### Configuration and secrets

Choose one authentication mode; the API refuses to start with neither.

`PROOFBENCH_INSECURE_DEV=1` is the default local profile: no token anywhere,
every request resolved to the single `PROOFBENCH_DEV_TENANT`. It authenticates
nothing, so it is supported only while the listener stays bound to `127.0.0.1`
on a machine with one operator. Any step that makes the deployment reachable by
someone else — a port forward, a LAN or `0.0.0.0` bind, an ingress — requires
unsetting it first.

`PROOFBENCH_API_KEYS` is required in that case, and maps tenant IDs to random
tokens of at least 32 characters. Supply it and provider credentials through the
platform's secret manager, not an image, Compose file, repository, or frontend
build arg.

The Compose profile passes secrets as container environment variables and is
therefore suitable only where the host and Docker control plane are trusted.
For managed production, inject them from the platform secret manager and
restrict environment/process inspection to the service identity.

The remaining supported values are documented in `.env.example`: cookie age,
state/ledger paths, deployment identity, request/run/storage quotas, retention,
upload bounds, provider endpoints, and models. `docker compose config` must show
the intended non-secret values before deployment. Do not paste rendered Compose
output into tickets because it can contain resolved secrets.

### Release procedure

1. Require green backend, frontend, CodeQL, container build/scan, and Compose
   smoke jobs for the exact commit.
2. Build images locally, or run the manual release workflow. Pushing a `v*` tag
   deliberately does not publish. The workflow additionally refuses to publish
   unless its checklist acknowledgement input is true, the LICENSE legal-name
   placeholder is resolved, and a reviewed `THIRD_PARTY_NOTICES.md` exists; none
   of those is satisfied today, so publishing currently fails by design. See
   [DISTRIBUTION_CHECKLIST.md](DISTRIBUTION_CHECKLIST.md). When it does run, it
   publishes both images with BuildKit SBOM and maximum provenance attestations.
   Save its digest manifest with the release. Set `PROOFBENCH_API_IMAGE` and
   `PROOFBENCH_WEB_IMAGE` to those exact `registry/repository@sha256:...`
   references; never deploy a mutable tag.
3. Put the application in maintenance/drain mode at the ingress and allow
   active runs to finish or stop them explicitly.
4. Run the pre-upgrade backup procedure below.
5. Start the new image with `docker compose up -d --wait --no-deps api`, then
   update `web` after authenticated readiness is green.
6. Verify liveness, authenticated readiness, sign-in, session creation, and SSE.
   To verify results and PDF download you must execute a real run — there is no
   demo mode. Use the opt-in live smoke test (`npm run test:e2e:live` with
   `PROOFBENCH_RUN_LIVE_SMOKE=1` and `PROOFBENCH_E2E_TOKEN`), which is bounded to
   local candidates but still incurs provider cost. Budget for it.
7. Keep the prior images and backup until the rollback window closes.

Schema startup performs ordered forward migrations documented in
[`server/MIGRATIONS.md`](../server/MIGRATIONS.md). Never skip versions. A
failed migration aborts readiness; restore the backup and prior image instead
of manually editing `PRAGMA user_version`.

### Health and traffic gating

- `GET /api/live` is public process liveness.
- `GET /api/ready` is authenticated and verifies auth configuration, SQLite,
  and writable run/dataset volumes.
- `GET /api/providers` reports provider readiness from configured environment
  and tenant credentials only. It contacts no provider and never issues a paid
  probe, so polling it costs nothing.
- The API image healthcheck derives a tenant token from its private environment
  and calls authenticated readiness without printing the token.
- Nginx starts only after API health succeeds. The external ingress should also
  gate traffic on an authenticated/internal readiness probe and alert when a
  container becomes unhealthy; Docker restart policy alone does not restart an
  already-running unhealthy container.

SSE proxy buffering must remain disabled and proxy timeouts must exceed the
longest allowed benchmark. Use a graceful stop window long enough for terminal
state persistence and sandbox cleanup.

### Backup and restore

Run backups at least daily and before every schema/image upgrade. Encrypt them
and store them outside the Docker host. Because local retention defaults to no
automatic expiry, backup expiry is the operator's own choice; pick one and apply
it consistently. Recovery objectives are a Part 2 concern and are not committed
to here.

For a consistent two-volume backup:

1. Drain new traffic and wait until no session reports `is_running`.
2. Stop the API: `docker compose stop api`.
3. Back up the `proofbench-runs` and `proofbench-data` volumes as one timestamped
   set using the platform's volume snapshot mechanism. The SQLite database,
   WAL, run artifacts, and sandbox ledger are all in the runs volume.
4. Record image digests, `PRAGMA user_version`, backup checksums, UTC time, and
   volume snapshot IDs in the backup manifest.
5. Restart and require `docker compose up -d --wait`.

If the platform cannot snapshot volumes, mount each stopped volume read-only
into a pinned backup utility container and create checksummed archives. Never
copy only the SQLite main file from a live WAL database.

Restore into new empty volumes, verify archive checksums, start the prior exact
image, and require authenticated readiness. Then test tenant isolation, session
and immutable run history, dataset ownership, and report/PDF reads before
routing traffic. Restore verification reads existing history; it does not
require a new run, since every new run is real and billable. Perform and document a restore drill at least
quarterly.

Provider credentials are not part of a data backup and must be restored from
the deployment secret manager.

### Monitoring and alerts

Every response carries a validated/generated `X-Request-ID`; request telemetry
is structured JSON and excludes query strings, authorization, cookies, bodies,
and filesystem paths. Collect stdout/stderr with access controls, and keep log
retention no longer than artifact retention.

Alert on:

- sustained readiness or container health failures;
- authentication and quota rejection spikes;
- failed/interrupted runs and provider timeouts;
- SQLite busy/migration errors, disk pressure, or backup failure;
- retention tombstones that remain pending across retry intervals;
- sandbox deletion/reconciliation failures or growing ledger age;
- unusual request latency/status-class rates.

Where `/api/metrics` is enabled, scrape it through an authenticated/internal
path only and avoid tenant, session, candidate, or URL labels that create high
cardinality or disclose benchmark data.

The supplied profile bounds API/web CPU, memory, PIDs, and JSON log files.
Tune the limits only after load testing and configure the central collector to
ship logs before Docker rotates five 10 MiB files per service.

### Incident handling (local)

Rotate exposed tenant/provider tokens immediately, invalidate browser sessions,
and inspect redacted request IDs, run IDs, and sandbox lifecycle records. Never
copy benchmark documents, raw request bodies, generated credential material, or
unredacted artifacts into an incident note. Preserve relevant logs and backups
before destructive cleanup.

This is engineering hygiene for a single-operator deployment. It is not an
incident response programme, and it carries no notification obligation, because
there is no one to notify. That changes under Part 2.

### Retention and deletion

`PROOFBENCH_RETENTION_DAYS` defaults to `0`, which disables automatic expiry so
that an operator's own data is not deleted on a horizon they did not choose. Set
a positive number to expire completed tenant data after that many days. Active
resources are never expired.

Database rows enter a durable deletion queue/tombstone before filesystem work;
failed deletions remain retryable and observable. Investigate any tombstone
older than two retry intervals.

## Part 2: future hosted and enterprise commitments

**None of this is in force.** ProofBench serves no third party, and nothing
below is offered, promised, or owed to anyone today. These are the obligations
that would have to be designed, contracted, and staffed before ProofBench were
operated for someone else, per
[ADR-0001](adr/0001-local-product-boundary.md#possible-future-paths).

Treat every item as unbuilt. Do not cite this section as evidence of a
capability, and do not quote a number from it to anyone.

### Recovery objectives (not committed)

- An RPO and RTO would be set by contract, not by this file. No target exists,
  and this document deliberately names no number: picking one is an operator and
  customer decision, and any candidate value has to be validated by measured
  restore exercises before it is committed to anyone.
- Restore drills would be performed and documented on a fixed cadence, at least
  quarterly, with results retained as evidence.
- Backup expiry would be bounded by the contractual retention period unless a
  documented legal hold applies.

### Availability and support (not committed)

- No support service or response commitment exists today. Nothing is owed to
  anyone, and no report is guaranteed a reply; see [SECURITY.md](../SECURITY.md).
- Any availability target, support window, or response time would come from a
  signed agreement. The scope of support has to be defined contractually before
  it is offered, not inferred from practice.
- On-call ownership, escalation paths, and a status communication channel would
  have to exist before any target is published.

### Incident response programme (not built)

- Defined severity levels, declaration criteria, and an accountable incident
  commander.
- Contractual and statutory breach-notification timelines, with counsel
  identified in advance.
- Customer-facing communication templates and a post-incident review process
  with retained records.
- Evidence preservation and legal-hold procedures that survive routine cleanup.

### Retention and disclosure (not built)

- A finite, disclosed retention horizon replacing the local default of `0`.
- A published privacy notice, terms of service, and a data processing agreement
  where the operator acts as a processor, with subprocessors disclosed.
- The remaining prerequisites in
  [DATA_HANDLING.md](DATA_HANDLING.md#future-launch-checklist-not-yet-satisfied)
  and [DISTRIBUTION_CHECKLIST.md](DISTRIBUTION_CHECKLIST.md).
