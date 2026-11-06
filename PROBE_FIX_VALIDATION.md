# Probe fix validation — 2026-11-06

Parent artifact: `397228bd3e0f3964b2ce14e6394d324af4ccb703` (serialized readiness fix). Main checkout remains unchanged. This package installs code only and does not authorize/launch a new account trial or apply recovery.

## Evidence

The user's `e85a00f2-6b8e-4b39-bac2-b958a001c8c0` trial recorded `FULL_PROTECTED_READY`, child stage `STREAM_OBSERVATION` / code `NONE`, and `FINALLY_RAN`. Its supervisor reported `DATABASE_LOCK_TIMEOUT`, child exit 0, kill count 0, no natural lease wait or protection probes, preserved history/baselines, final flat, released leader and EMERGENCY/paused. This is **not accepted** protected-child fault evidence.

A negative control used the **unchanged deployed `9c2645…` runtime** in an isolated temporary copy, the production supervisor/child path, real PostgreSQL and OS child, and a loopback synthetic venue. A test-only transaction held the account row for 2.6 seconds immediately before the competing-leader `acquire`. The old supervisor returned `DATABASE_LOCK_TIMEOUT`, child exit 0, kill count 0, cleanup NONE, VERIFIED_FLAT and EMERGENCY/paused. The test requiring a real kill failed as intended (5.08s; earlier identical run 5.49s). New diagnostic-field assertions were omitted only in the temporary old-runtime control because that old runtime lacks those fields; the kill/fencing/flat safety assertions were retained. The shipped new-code test is unchanged and includes the diagnostic assertions.

This reproduces the observed result pattern at the candidate supervisor path. No production stack trace was collected, so it does not assert the exact failed Windows call as a fact.

## Passing checks

90 distinct focused cases passed across these runs:

- 68 execution/scope/serialization/probe regressions in 273.15s. Includes four actual OS SIGKILL scenarios with real PostgreSQL, original natural 60s lease, and loopback synthetic exchange/WSS: ordinary handoff, target trigger during wait, failed restore, and a contended pre-kill acquire. The latter records one real SQLSTATE55P03 followed by an actual active-lease rejection, then SIGKILL, natural wait, next fence, stale owner/renew rejection, same IDs in a new session and final flat. Target/restore failures remain NOT_ACCEPTED.
- Two newly added full protected-child failure cases in 2.23s: protection changes at the final pre-kill proof, or the competing-leader probe remains inconclusive. Both perform **zero kills**, no second entry, and preserve cleanup/paused behavior.
- 20 installer guard cases in 7.81s: read-only canonical review, unchanged account state, no token output, closed exact scoped order/ready/finally/child-failure markers, no active owner, and rejection of mismatched scope/evidence. No recovery-apply command is called.

The first run includes ten real PG probe tests (also run separately in 10.78s; not counted twice): original acquire produces 55P03 under the preserved two-second lock limit; retryable timeout is not treated as successful lease rejection; continued contention fails; an unexpected successful competitor acquisition fails; dead child/changed owner/expired lease prevent any probe; non-55P03 DB failures are never retried. In the contention test, account owner, fence, lease_until and ledger remain byte/value-equivalent before and after the probe.

Ruff checks and formatting pass on six touched Python files. Strict mypy passes on the runtime and unchanged CLI. This is a focused regression set, not a full repository suite. No real exchange calls, API keys, SMTP authentication or mail delivery were used in cloud tests.

## Preserved sources and limits

New runtime SHA256: `70d9f0a6ec74223e8a7a82ec55c976288c10fb13c01c9b478f49f1a0fd408709`.

Byte-for-byte comparisons against the installed parent confirmed unchanged persistence (`40f9a33…`), stream (`409098c…`), validation, protected-worker CLI, DEMO config and exchange client. The DB lock timeout, natural lease, WSS event processing, freshness checks, risk thresholds, entry/protection/exit caps, migrations and locked specification are unchanged. The implementation does not pause WSS dispatch or suppress protection failures to pass the fault test.

Only SQLSTATE55P03 from a competing acquisition can be re-observed in a finite window. It remains inconclusive until a real `RiskRejected` from the original acquire has occurred. The code rechecks child/owner/fence/live lease, then signed entry/position/original SL/TP identity immediately before the kill stage. New attempts/timeouts/pre-kill-stage fields expose this distinction. There is no order retry, execution-child retry or automatic recovery acknowledgement.

Windows Smart App Control remains enabled; downloaded CMD launchers were removed. Manual install instructions use normal file hashing and Docker commands, without security-setting changes. Windows installation remains pending. Actual account SIGKILL recovery, partial-fill protection and actual TP-fill/original-SL cleanup remain unverified.
