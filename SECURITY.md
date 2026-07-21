# Security policy

## Scope

ProofBench is proprietary software (see [LICENSE](LICENSE)) maintained by one
individual developer. There is no public or hosted ProofBench instance to test
against, and no domain, so this policy covers the source in this repository and
deployments the copyright holder runs. Do not test any host you are not
authorised to test.

Support is best effort and pre-release. Reports are read and acted on when the
sole developer has time, and there is no bug bounty, no response-time
commitment, no fix-time commitment, and no support SLA. Do not rely on a
response. This is a consequence of the project's stage, recorded in
[docs/adr/0001-local-product-boundary.md](docs/adr/0001-local-product-boundary.md),
and it would have to change before ProofBench is offered to anyone else.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Report it privately to
the copyright holder through the same private channel you received this software
through. Where this repository is hosted on GitHub with private security
advisories enabled, use that reporting flow. Include reproduction steps,
affected endpoints, and any evidence of data or credential exposure.

Do not include real benchmark documents or datasets, provider credentials, or
access tokens in a report. Replace them with synthetic examples.

## Supported versions

Until the first stable release, only the latest commit on `main` receives
security fixes. A version support table will be published with the first
production release.

## Operational response

Credential exposure should be treated as an incident: disable affected access,
rotate provider credentials, preserve relevant audit logs, and review sandbox
and job activity before restoring service.
