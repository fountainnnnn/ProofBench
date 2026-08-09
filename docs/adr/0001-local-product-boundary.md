# ADR-0001: Local product boundary

- Status: Superseded for hosted trials by ADR-0002; retained for local operation
- Date: 2026-07-20
- Deciders: the copyright holder (sole developer and operator)
- Supersedes: none

## Context

ProofBench is written and run by one individual developer. The source is
publicly visible but proprietary; see [LICENSE](../../LICENSE). There is no
company, no legal entity, no domain, no hosted instance, and no third-party
user. Several documents in this repository had drifted into wording that
implied a running service with customers, availability targets, and support
obligations. None of that exists.

At the same time, the code genuinely carries the seams a multi-tenant product
would need: tenant-scoped ownership, pluggable providers, disposable sandboxes,
provenance labelling, and retention machinery. Those seams are real and tested.
The risk was describing them as delivered service commitments rather than as
implemented technical controls.

The decision this ADR records is where the product boundary sits today, so that
documentation, UI copy, and release tooling stop overstating it.

## Decision

ProofBench is a **proprietary, source-visible, solo-operated, local
pre-release**. Its supported shape is **one hardened single-host Compose unit**:
one FastAPI replica, one Nginx replica, SQLite WAL, and named volumes on one
durable host.

The following seams stay stable, because they are what any future path would be
built on. They are architecture, not marketing:

| Seam | What is stable |
|---|---|
| Schema | Ordered forward migrations with a recorded `PRAGMA user_version` |
| Auth | Fail-closed tenant identity in one of two explicit modes; `local` is tokenless and loopback-only, `authenticated` keeps bearer or API key for writes and read-only cookie transport |
| Storage | Server-issued dataset and run IDs; no client-selectable host paths |
| Provenance | Real-only: new results persist `measured`, `synthetic` is read-only history; unproven states withhold metrics |
| Retention | Durable tombstones and a retryable deletion queue, driven by one configured horizon |
| Provider | Credential entitlements are exact and server owned, never derived from user input |
| Sandbox | One disposable sandbox per candidate, with a durable ownership ledger |

Changing any of these is a contract change and must update
[CONTRACTS.md](../../CONTRACTS.md), the tests, and the operations docs in the
same change.

### Authentication default

The operator is the only user, on one loopback-bound host. Making that operator
mint and re-enter an API token to reach their own console is a credential with
no one to authenticate against. So local and on-premises single-operator
operation defaults to `PROOFBENCH_INSECURE_DEV=1`: tokenless, one deterministic
tenant, no sign-in gate and no sign-out control in the console.

This is a property of the loopback boundary, not of the code. The authenticated
mode is not removed or deprecated — it stays the tested path that Path A and
Path B are built on, and it is required the moment the deployment is reachable
by anyone but the operator. Tenant scoping and server-owned provider credential
entitlements apply identically in both modes.

### Retention default

Local and on-premises operation defaults to `PROOFBENCH_RETENTION_DAYS=0`,
meaning **no automatic expiry**. An operator's own benchmark data must not
disappear on a horizon they never chose. A finite retention horizon is a
property of a hosted offering, where it is disclosed contractually, not a
default imposed on a local operator.

## Non-goals (current)

None of the following is built, offered, or claimed today:

- A hosted or multi-tenant ProofBench service.
- Any availability, latency, RPO, RTO, or support commitment to anyone.
- An enterprise licence, a data processing agreement, or a published privacy
  notice or terms of service.
- Multi-host scaling: network database, object storage, external queue, or
  external secret manager.
- A named legal entity, governing jurisdiction, domain, or support contact.
- A bug bounty or a security response-time commitment.

Documentation may describe these as decisions or as future prerequisites. It
must not describe them as features.

## Possible future paths

Both paths remain open. Neither is committed to, and work on either should not
start before its entry criteria are met.

### Path A: hosted SaaS

| Stage | Exit criteria |
|---|---|
| A0. Decide | A named legal entity exists, with a governing jurisdiction and a security contact. |
| A1. Legal base | Published privacy notice, terms of service, and a data processing agreement reviewed by counsel. Every provider confirmed as a lawful recipient and disclosed as a subprocessor. |
| A2. Multi-tenant hardening | Tenant isolation tested adversarially, not merely implemented. Finite retention default set and disclosed. Quota and abuse controls proven under load. |
| A3. Multi-host | Network database, object storage, external job queue, and external secret manager, with the seams above preserved. |
| A4. Operate | Backup and restore drilled, monitored, and alerting. Only at this point may availability or response targets be published, and only as the contract states them. |

### Path B: licensed enterprise / on-premises

| Stage | Exit criteria |
|---|---|
| B0. Decide | Same as A0. |
| B1. Commercial | A written licence agreement prepared with counsel. [docs/DISTRIBUTION_CHECKLIST.md](../DISTRIBUTION_CHECKLIST.md) fully satisfied. |
| B2. Distributable build | Reviewed third-party notices shipped with the artefact. Reproducible, digest-addressed images. |
| B3. Handoff | Installation, upgrade, migration, and backup procedures validated by someone other than the author. Retention stays operator-chosen and defaults to no expiry. |
| B4. Support | A support model is defined and contracted, or explicitly excluded in writing. |

## Anti-overengineering rules

These exist so that keeping the seams stable does not become an excuse to build
the future product now.

1. **Do not build for a tenant that does not exist.** A seam is kept stable;
   it is not extended speculatively. New generality needs a present-day caller.
2. **One host until a second one is required.** No distributed component enters
   the tree before Path A3 is entered deliberately.
3. **No abstraction with one implementation** unless a contract in
   `CONTRACTS.md` names the boundary.
4. **Describe only what runs.** Documentation states current behavior. Future
   work is labelled as a decision or a prerequisite, never in the present tense.
5. **No commitment without a contract.** Availability, retention, response, and
   support language belongs in a signed agreement, not in a repository file.
6. **Delete rather than defer.** Unused scaffolding for a future path is removed,
   not commented out or feature-flagged.

## Consequences

- Documentation, UI copy, and release tooling are corrected to describe a local
  pre-release. See [ARCHITECTURE.md](../../ARCHITECTURE.md).
- The retention default of `0` is documented consistently with the code.
- Publishing images requires an explicit acknowledgement and passes only when
  the LICENSE placeholder is resolved and reviewed third-party notices exist.
- Contributors gain a clear test for scope: if a change serves only a future
  path, it does not land yet.
