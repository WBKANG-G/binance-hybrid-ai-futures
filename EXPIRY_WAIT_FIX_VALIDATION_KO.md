# Expiry wait fix validation — 2026-11-03

Final scoped suite: 96 passed in43.67s, failed0/skipped0. Covers expiry scheduling8, EventInbox10, private stream faults, local WS/PG coalescing, cancellation while DB workers drain, disconnect handling, actual TP-check synthetic contracts, baseline DEMO coordinator/validation, and two new PostgreSQL expiry/fill-race tests. Ruff check/format3 changed Python files and strict mypy stream passed.

New PG tests cover unfilled expiry and unseen partial fill racing expiry: residual entry canceled, cumulative fill reconciled, original quantity retained, verified SL/TP present, no protection DELETE, no replacement entry, repeat expiry no transport. No Binance request or real order was made.

Test development failures retained: initial new test referenced nonexistent sl_verified/tp_verified attributes; assertions corrected to actual protected state and full normalized SL predicate plus TP venue fields. Boolean normalization was corrected for synthetic raw algo responses. A combined run initially failed collection on duplicate unit/integration module basename; integration test renamed. No existing failing assertion was deleted or weakened. Final exact combined96 passed. Earlier separate54 passed30.13s and existing41 of42 passed14.62s before new test assertion correction.

Only services/execution/demo_stream.py differs among495 parent manifest files. Added2 tests, activation CMD and2 guides; regenerated transfer manifest. No dependency/config/migration/risk/TTL/quantity/price changes. Full app regression and production Docker rebuild were not run; Docker installation is pending Windows execution. Earlier unrelated monolithic suite timing failures are not resolved by this scoped success.

Scheduling checks after on_ready, before receipt wait, and before ping/reconcile on timeout. Wait is clamped to1ms..5s and nearest remaining entry expiry. Filled entries with no remaining quantity do not shorten waits. Event REST reconciliation remains before existing end-of-loop expiry, and active threaded operations still drain. Does not preempt blocking network/DB or promise exact venue cancellation deadline.

Historical Windows op6703f8c0-0c8a-4a2b-84d5-37e39693dbba remains NOT_ACCEPTED/TP_FILL_NOT_OBSERVED, canceled/zero fill/VERIFIED_FLAT/NORMAL/paused. This code change is not a new TP acceptance. Main and user DB/history untouched by cloud work.
