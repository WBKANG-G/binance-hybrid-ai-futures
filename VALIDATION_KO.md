# Validation — 2026-11-03

Actual cloud Docker Compose runtime using existing offline fault-check image, dedicated internal network and tmpfs PG: 96 passed in39.17s, failed0/skipped0. Summary ISOLATED_SYNTHETIC_EXPIRY_CHECK_PASSED, checked_base_files273/overlay_files3, real_exchange_calls0. Existing test fixtures use synthetic exchange injection and loopback WS. Source overlay exactly matches deployed expiry fix and its two new tests. Collection includes prior core DEMO, validation, TP synthetic checks, WSS/worker drain/disconnect plus expiry wait and partial fill race.

Runner guard tests8 passed0.01s. Actual Docker LIVE_TRADING_ENABLED=true run returned fixed BLOCKED_SCOPE_IMAGE_OR_TEST_DATABASE with exit2 before source/DB processing. Ruff check/format passed. Scope rejects missing opt-in, foreign test DB, DATABASE_URL, any BINANCE_ environment, non-PAPER, LIVE flag, key directory, wrong working directory. Source mismatch blocks before pytest.

Network inspect internal=true; DB no host PortBindings and /var/lib/postgresql tmpfs. Compose config checks read_only=true, cap_drop ALL, no host/key/volume mounts. One-off checks auto-removed; DB/network cleanup successful. Attempted post-run container inspection found no checks container because --rm had already removed it; runtime read-only setting is established from Compose configuration, not a surviving container inspection.

Windows source update evidence: API built/Healthy, EXPIRY_WAIT_FIX_SOURCE_VERIFIED; readiness dayNov3/weekNov2 matched, NORMAL/paused/empty, no DB change, private tmpfs key retained, signed GET22/connection1/reuse21/1380ms. This is user evidence for deployment only; Windows execution of this96-test runner is pending.

Runtime tests preceded comment-only exception redaction annotation and Ruff formatting of the generated runner; embedded app/tests unchanged. No dependency/app/production source modification from this runner work. Prior actual TP remains unfilled/NOT_ACCEPTED/VERIFIED_FLAT. No real trade or LIVE request made. Main unchanged.
