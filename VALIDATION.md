# Validation — 2026-11-05

Existing audited reader reused; fixed target changed to bd2a629e-7d8f-4b60-9504-8de2d12f780b. Audit timestamp description corrected from transaction start to after row lock/before apply, matching DemoStore.change. No production app code/install/config changes.

7 scoped synthetic transport + real dedicated PostgreSQL tests passed3.12s: actual READ ONLY transactions and unchanged financial rows/events/outbox; one historical GET; no cached client/no network; invalid identity/missing or reversed timestamps; exception redaction; write-enabled client rejected without transport. Ruff check/format project config and strict mypy passed. Final test formatting only wraps the corrected label assertion. No actual Binance calls or account changes in cloud.

Previous Windows source installation and96 isolated tests remain accepted in their scopes. Current real TP remains unfilled/NOT_ACCEPTED/finalflat; this diagnostic's Windows results pending. Old timing values in guide are historical user evidence, not newly measured outputs. Single-run clock/load differences limit comparison.
