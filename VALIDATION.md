# Validation — 2026-11-05

20 scoped synthetic tests passed0.17s; failed0/skipped0. Ruff project check/format and strict mypy passed. Covers minimum size and thin top as non-guarantee, cap/maxLOT rejection, malformed/wrong-symbol/stale/future/boolean/nonfinite/zero/nonstep/crossed/invalidtick quotes, max12 samples/14 total public requests, no APIkey/signature, no account/order/session routes, deadline stopping next request, and first429 stopping without retry. Private session denied even if client constructed with trading-enabled config.

No DB/real credentials/private account or actual order calls in cloud. One direct SDK public connectivity attempt made before helper preparation failed at network/clock probe; no market data accepted. It produced signed_requests0/order_writes0. Existing client public transport sends key=None. TLS/host/routing/no-redirect/current clock protections preserved.

This script does not certify execution eligibility: public LOT/minNotional estimate only; no mark-price percent-price/account/margin/risk assessment. Sampling reports are not executable signals and never claim partial fills. Actual Windows public sampling remains pending.

Previous user-confirmed recovery remains NORMAL/paused, old4 request states preserved and acknowledged, no active exchange exposure as of its readiness check. Public probe does not recheck that private state. Main/application unchanged.
