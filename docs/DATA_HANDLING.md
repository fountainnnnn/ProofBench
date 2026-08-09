# Data handling and retention

ProofBench processes user-provided images, ground-truth CSV files, benchmark
outputs, generated adapters, logs, reports, and provider credentials.

## Current deployment

ProofBench supports local operation and one narrow Railway deployment for an
invited client's end-to-end trial. In the trial, Railway PostgreSQL stores
transactional state and a Railway volume stores uploads and artifacts. Daytona
and the configured model/search/extraction providers receive the data described
under third-party egress. The technical controls below are implemented; the
operator must separately disclose the actual region, providers, retention, and
deletion terms to the invited client before accepting their documents.

## Storage boundaries

- Uploaded datasets and artifacts are owned by one authenticated tenant.
- API lookups apply ownership before returning whether a resource exists.
- Clients refer to datasets by server-issued identifiers, never host paths.
- Generated code runs in disposable sandboxes with candidate-scoped
  credentials and receives only the selected dataset.
- Provider secret values are redacted before events or artifacts are persisted.

## Retention and deletion

`PROOFBENCH_RETENTION_DAYS` controls automatic expiry of completed tenant data.

The default is `0`, which means **no automatic expiry**: data is kept until the
operator deletes it. Local and on-premises operation defaults this way on
purpose, so that an operator's own benchmark data is never removed on a horizon
they did not choose. Setting a positive number enables expiry after that many
days. Active runs are never removed by retention cleanup. The Railway trial
must use a positive, disclosed value; the example runbook uses seven days. See
[ADR-0002](adr/0002-railway-client-trial.md).

Deleting a session queues removal of its conversation, immutable run records,
reports, and run artifacts. Dataset deletion queues both registry metadata and
the owned upload directory. Database tombstones remain retryable until the
filesystem work succeeds; failed deletions are logged and surfaced to
operations. With the default of `0`, backup expiry is the operator's own choice.
Where a retention commitment exists in a future deployment, backup expiry must
not exceed it unless a legal hold applies.

## Third-party egress

A benchmark run sends data outside this deployment. This is inherent to what
ProofBench does, not incidental:

- Uploaded documents are copied into a disposable sandbox for each candidate
  attempt, and to any hosted extraction candidate the run includes.
- Candidate discovery and documentation scraping issue search and fetch requests
  to the configured search/scrape provider.
- Adapter generation and report writing send prompt content, which can include
  candidate names, documentation excerpts, and result summaries, to the
  configured model provider.
- Provider secret values are redacted before events or artifacts are persisted.
  Redaction governs what ProofBench stores and emits. It does not limit what a
  provider receives in the request itself.

Each provider processes that data under its own terms, which ProofBench neither
restates nor guarantees. An operator who enables a provider is responsible for
confirming that sending it their data is lawful and permitted. Disabling a
provider is the only way to stop egress to it.

## Third-party launch checklist

Every applicable item must be addressed before ProofBench processes a client's
documents. A synthetic-data technical smoke may happen first.

- A published privacy notice identifying the operator by legal name,
  subprocessors, deployment regions, lawful basis, data-subject rights, a
  security contact, and contractual retention.
- Published terms of service, and a data processing agreement where the operator
  acts as a processor.
- A named legal operator and governing jurisdiction. Today the copyright holder
  is an individual developer, and no legal entity, jurisdiction, domain, support
  contact, or availability commitment has been established.
- Confirmation that each provider used in a deployment may lawfully receive the
  data sent to it, and disclosure of those providers as subprocessors.
- The multi-host requirements in [CONTRACTS.md](../CONTRACTS.md) section 8, if
  the deployment serves more than one tenant.
- The distribution prerequisites in
  [DISTRIBUTION_CHECKLIST.md](DISTRIBUTION_CHECKLIST.md), if a build is sold,
  handed to a company, or published.

These are legal gates, not engineering tasks. Each requires a decision by the
copyright holder and, for the contractual items, review by counsel. They cannot
be satisfied by writing more code, and no amount of technical hardening
substitutes for them.

This repository document describes technical behavior. It is not legal terms, a
privacy policy, or a compliance assessment, and it is not a substitute for legal
counsel.

Do not use another party's datasets to train models unless a separate, explicit,
revocable agreement permits it. Automated tests must use synthetic input data
only. The routine end-to-end suite additionally contacts no provider; the opt-in
live smoke test does, and runs against the synthetic sample dataset rather than
any real customer data.
