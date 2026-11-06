# Validation —2026-11-06

8 tests passed using real PostgreSQL isolated schemas and synthetic execution fixtures. Read-only repeatable-read transaction, exactly4 SELECTs after2 SETs, operation-bound timeline, account/events/outbox/urgent alerts/attempts unchanged and no exchange calls verified. Scoped operation mismatch refuses results; a later owner/fence failure is excluded from the trial timeline. Private ledger values/invalid diagnostic schema/numeric data are redacted; absent order is not reported as a fabricated zero fill. CLI private failures sanitized and LIVE context blocked before DB access.

Ruff check/format passed with the deployed app rule set. No app/config/dependency changes. Diagnostic executes via STDIN in the existing API container. Initial external fixture run required correcting its working directory to the application root for Alembic; no production gate or test assertion was weakened.

This helper projects only allowlisted fields from local durable data. It does not infer a child transport POST count from the parent's zero counters. No actual Windows data, exchange query, order or risk unlock was performed by cloud testing. Windows report remains pending. PostgreSQL audit timestamps are local after-row-lock/pre-apply timestamps, not exchange/commit timestamps.
