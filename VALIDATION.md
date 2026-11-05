# Validation — 2026-11-05

Combined exact final helper + existing EventInbox/event-clock suite:61 passed1.08s; includes23 helper tests previously passed0.86s. Ruff check/format project configuration and strict mypy passed. All exchange transport synthetic; dedicated real PostgreSQL fixture used only in cloud. No user account/network requests or production API source changes in cloud.

Tests cover e/E+numeric clock allowlist and secret-field omission; invalid/unknown/expired/boolean event timestamps; no-event not-accepted; original stale/future rejection; frame/count bounds; POST/DELETE order/algo/leverage denied before transport; actual session supervisor with synthetic socket/PG then replay; readiness state/risk/date/occupancy gates; fixed error redaction; true READ ONLY financial snapshot transactions, metadata-only stability and detected high-water change.

Initial tests exposed replay client missing synced_at, which existing clock validator requires. Replay now derives synced_at from the recorded verified probe after_ms, matching DemoClient.sync_clock; all original assertions retained. Initial strict typing issues on dynamic wrapper exports were resolved by an explicitly typed dynamic import, and previous module session binding is restored in finally.

No full app regression or Windows observation run yet. Production stream/inbox/clock/observer/readiness5 file hashes must match deployed versions before execution. Runtime private observation allows listen-key lifecycle and lease/audit metadata, all order/account mutations denied by existing ObservationClient. Financial preservation is checked after observation; does not roll back concurrent changes.

Capture is a deliberately limited hint envelope, not full private-frame archival/replay. No events => NO_EVENTS_NOT_ACCEPTED; never inflate no-event idle session to actual fill replay. Main unchanged, TP/partial-fill/protected-position actual gaps remain.
