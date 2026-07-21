# Server state migrations

ProofBench stores the schema version in SQLite's `user_version`. Server startup refuses to
open a database whose version is newer than the running code. Upgrades are idempotent and are
serialized with a SQLite write transaction so multiple API workers can start against the same
database safely.

Current schema version: **4**. Version 4 makes run provenance an authoritative
SQLite field so missing or corrupt filesystem artifacts cannot cause synthetic
results to be reported as measured. Legacy rows without authoritative
provenance are exposed as unverified and must be rerun before use as evidence.

Before an upgrade, the server performs a SQLite online backup beside the database using the
name `proofbench.sqlite3.pre-v<old>-<timestamp>-<worker>.bak`. This is enabled by default. Set
`PROOFBENCH_MIGRATION_BACKUP=0` only when the deployment already provides a verified,
point-in-time database backup. Startup runs `PRAGMA integrity_check` after the upgrade and
fails closed if it does not return `ok`.

For a planned upgrade:

1. Stop write traffic or put the API in maintenance mode.
2. Verify free disk space and the most recent external backup.
3. Start one new worker and confirm migration and integrity-check success.
4. Start the remaining workers, then retain the pre-migration backup through the rollback
   window.

To roll back, stop every worker, archive the failed database and its WAL/SHM companions for
diagnosis, restore the verified pre-migration backup to the configured `PROOFBENCH_STATE_DB`
path, and restart the previous server version. Never run an older server against a newer
database in place; the forward-version preflight intentionally rejects that configuration.
