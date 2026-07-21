# Distribution checklist

ProofBench is proprietary and pre-release. Today it is built and run locally by
its copyright holder. No authorized binary, container image, commercial licence,
enterprise handoff, or hosted instance has been distributed to anyone. See
[ADR-0001](adr/0001-local-product-boundary.md) for the current boundary.

This is not a claim of secrecy. The source is in a publicly visible repository:
anyone can read, clone, and build it, and the LICENSE governs what they may then
do with it. Treat the source as already public. The "nothing is distributed"
statements in this document are limited to the supported and authorized channels
listed below — they say nothing about what a third party can already fetch.

This checklist applies before **any** of the following:

- selling ProofBench, in whole or in part;
- handing a build to a company under an enterprise or on-premises licence;
- publishing a binary, container image, or installable artefact anywhere the
  public can obtain it;
- launching a hosted instance that anyone other than the copyright holder uses.

Local development builds and CI are unaffected. Nothing here blocks running
`docker compose build` on your own machine.

None of these items is satisfied yet. Each is a prerequisite, not a task in
progress.

## 1. Ownership and identity

- [ ] Replace the `[LEGAL NAME NOT YET SET]` placeholder in [LICENSE](../LICENSE)
      with the copyright holder's actual legal name. Do not invent one, and do
      not guess an entity type.
- [ ] Decide whether the distributing party is the individual or a legal entity,
      and record the governing jurisdiction.
- [ ] Establish a security contact that will still be monitored after the
      handoff, and update [SECURITY.md](../SECURITY.md).

## 2. Commercial agreement

- [ ] Have counsel review the commercial agreement covering the sale, licence,
      or hosted offering. The LICENSE file is a statement of position, not a
      negotiated contract, and is not a substitute for one.
- [ ] Have counsel confirm the warranty disclaimer and liability limitation are
      enforceable in the governing jurisdiction and against the intended
      counterparty.
- [ ] Decide, in writing, what support and availability (if any) is being
      committed to. Silence is not a commitment; an unreviewed document can
      accidentally become one.

## 3. Third-party licences and notices

- [ ] Generate the complete set of third-party runtime licences and notices for
      the exact artefact being distributed. Manifests (`requirements*.txt`,
      `web/package.json`, `web/package-lock.json`) and CI SBOMs identify the
      components in use; they are **not** themselves the required notices, and
      their existence does not mean notices have been delivered.
- [ ] Cover every layer of the artefact: Python dependencies, npm dependencies,
      container base images, system packages installed into the image, fonts,
      and any bundled model or dataset.
- [ ] Have a human review the generated output. Automated tooling misses
      vendored code, dual-licensed components, and licences with attribution or
      source-offer obligations.
- [ ] Confirm each licence's obligations are actually met, including any
      copyleft, attribution, notice-retention, or written-offer requirement.
- [ ] Write the reviewed result to `THIRD_PARTY_NOTICES.md` at the repository
      root and ship it with the artefact. The release workflow refuses to
      publish without it. Do not create a placeholder or partial file to satisfy
      the check: an incomplete notice file is worse than an absent one, because
      it asserts a review that did not happen.

## 4. Trademarks and endorsement

- [ ] Verify that every third-party name used in product source, documentation,
      UI, and pitch material is used nominatively, to describe an integration,
      and does not imply sponsorship, partnership, endorsement, or certification.
- [ ] Confirm no third-party logo, wordmark, or brand asset is redistributed
      without permission.
- [ ] Confirm hackathon-related naming carries no ongoing affiliation claim once
      the event has passed.
- [ ] Keep the no-endorsement disclaimer present wherever third-party names are
      listed together.

## 5. Privacy, terms, and operator inputs

Required where the deployment processes another party's data. For a purely
on-premises licence where the licensee is its own operator, confirm in writing
that these are the licensee's responsibility rather than assuming it.

- [ ] Publish a privacy notice identifying the operator by legal name,
      subprocessors, deployment regions, lawful basis, data-subject rights, a
      security contact, and contractual retention.
- [ ] Publish terms of service, and a data processing agreement where the
      operator acts as a processor.
- [ ] Confirm each enabled provider may lawfully receive the data sent to it,
      and disclose those providers as subprocessors.
- [ ] Document the retention default the deployment ships with. Local and
      on-premises defaults to `PROOFBENCH_RETENTION_DAYS=0` (no automatic
      expiry); a hosted offering uses a finite, disclosed horizon.
- [ ] Complete the remaining operator inputs in
      [DATA_HANDLING.md](DATA_HANDLING.md#future-launch-checklist-not-yet-satisfied).

## 6. Release mechanics

- [ ] Satisfy the multi-host requirements in [CONTRACTS.md](../CONTRACTS.md)
      section 8 if the deployment will serve more than one tenant.
- [ ] Confirm the release workflow's acknowledgement input is being set by a
      person who has actually worked through this checklist, not as a formality.

This document is an engineering checklist. It is not legal advice and does not
substitute for counsel.
