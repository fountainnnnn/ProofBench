# ADR-0002: Railway client-trial deployment

- Status: Accepted
- Date: 2026-08-09
- Deciders: the copyright holder
- Supersedes: ADR-0001 only for the current deployment boundary

## Context

ProofBench now needs a short, invited-client end-to-end trial. This is a SaaS
deployment operated by the author, not a software distribution or a licensed
on-premises build. The trial needs a public HTTPS console and durable state,
but it does not justify horizontal workers, general availability, or an SLA.

## Decision

The supported hosted-trial shape is one Railway application service, one
Railway PostgreSQL service, and one Railway volume mounted at `/app/runtime`.
The application image contains the built React console and FastAPI API, binds
Railway's injected `PORT`, and runs as one non-root application process after
preparing the mounted volume. PostgreSQL is authoritative for transactional
state. The volume holds uploaded datasets, run artifacts, reports, and the
sandbox ownership ledger.

The public deployment always uses authenticated mode. One random token of at
least 32 characters identifies the invited client tenant. The browser holds
that bearer in memory only; an HttpOnly cookie authorizes read-only transports.
A reload therefore requires token re-entry before writes resume.

This shape deliberately stays at one application replica. Railway volumes are
single-service state and the in-process run worker is not a distributed queue.
Deploys may briefly interrupt the UI or SSE connection; durable events and run
state allow reconnection, but no availability target is promised.

The local Compose/SQLite shape from ADR-0001 remains supported for development
and solo operation. This ADR does not authorize public registration, multiple
unrelated customers, production claims, an SLA, or processing data before the
operator has made the necessary privacy, provider, retention, and contractual
disclosures to the invited client.

## Consequences

- `DATABASE_URL` or `PROOFBENCH_DATABASE_URL` selects PostgreSQL; SQLite remains
  the local default.
- Railway uses the public, detail-free `/api/deploy-ready` healthcheck.
- A stable `PROOFBENCH_DEPLOYMENT_ID` and the runtime volume are mandatory for
  sandbox cleanup and artifacts.
- PostgreSQL and the runtime volume require separate backup procedures.
- Horizontal scaling requires object storage and an external job queue first.
