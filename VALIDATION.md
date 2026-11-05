# Validation — 2026-11-05

Exact scoped tests:37 passed8.01s, failed0/skipped0. Includes10 new recovery orchestration tests,7 token-bound detail tests and20 existing canonical recovery tests. Dedicated real PostgreSQL, synthetic exchange only; no user DB/actual exchange actions in cloud. Ruff check/format4 Python files and strict mypy2 execution files passed.

New fixture reproduces four linked historical requests with the approved October20 zero-fill/unknown-exit pair and October31 two NOT_SENT/full-fill/closed records, plus latest closed opposite-order finish marker. Real canonical detail review and acknowledge_flat_emergency are called; tests verify NORMAL/paused, exact requests/orders/value/baselines/high-water preservation, added receipt only, GET-only/no asyn transport, release and idempotency. Changed set/count/evidence/new-operation/active-order/no acknowledgement/remote position/token change cannot apply; post-apply failure reports possible completion and no automatic retry. Readiness is a mocked post-release check in these fixture tests; production readiness uses the unchanged installed CLI.

Detail helper gains optional in-memory on_token callback only after original token binding/count/account-unchanged checks. Default readonly CLI still returns no token and cannot apply. No original application source/config/DB migration/risk policy modifications. Canonical source4 hashes gate main execution.

Additional actual isolated Docker loader checks (network none, readonly root, no keys, tmpfs /tmp) verified the same reference/helper upload and stdin workflow: missing key => BLOCKED_BEFORE_APPLY/exit2; tampered helper => HELPER_SOURCE_NOT_VERIFIED/exit2 before helper code executes; no stderr/raw secret output. Temp directory/container cleaned up. Windows CMD itself and user-side application remain pending.

Fixed recovery operation e0e27c9a-8616-48bb-aab7-5f061bff908b. Two original bounded audits are used, not three: fresh detail callback provides exactly the canonical token; apply independently rechecks under its lease. Reference is prior user report, no screenshot transcription. No actual order, unlock or remote deploy done by cloud tests. Main unchanged.
