# Change audit log

Every change to this repository's behaviour, data handling or credentials,
newest first. Model logic changes must say what changed and why.

## 2026-11-04 · Stage 2 Phase 1 · database protection

- Backup point before Stage 2: branch `backup/pre-stage2` at `936f5dc` (left untouched).
- Added `scripts/db/` (fingerprint, backup, migrate, CI setup) and
  `tests/db/` (end-to-end test on synthetic data).
- Added workflows: `db-scripts-test.yml` (tests on Postgres 16 and 17),
  `db-backup.yml` (daily encrypted backup with restore test; idle until
  its secrets are set), `db-migrate.yml` (manual, confirm with MIGRATE).
- No change to the API, models, tables or stored data. The live service
  is not switched to a new database by this change.
- Reason: the Render free Postgres expires 2026-11-30.
