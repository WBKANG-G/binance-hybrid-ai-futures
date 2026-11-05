# Validation — 2026-11-05

New detail reader6 tests plus existing canonical recovery20 tests:26 passed5.24s on dedicated real PG and synthetic exchange; failed0/skipped0. Ruff check/format and strict mypy passed. No Binance/user DB calls in cloud.

Checks4 unresolved linked/unlinked classifications, GET-only/no asyn transport, unchanged financial rows/events/outbox/owner/fence/lease, canonical token binding, rejected token mismatch, restored client/snapshot hooks even on transport failure, readonly-client requirement before transport, local order role link, credential exception redaction. Test fixture itself performs synthetic setup writes; the reader does not.

Same canonical service and original risk/occupancy/transfer/history/period/query budgets remain unchanged. Hooks observe existing results and restore in finally; no extra API requests for classification. Canonical token recreated with final fingerprint (original service requires second/final equal) and all request observations; exact match and counts required. No recovery token or apply operation is returned/performed.

Actual Windows prior full review:audited78/unresolved4/risk PASS/flat, HTTP336/connection1/reuse335/19027ms. Windows detailed classification remains pending. Raw identities are replaced by informational16-hex SHA references, not used to bypass canonical identity checks. Original source4 file hashes gate execution. Main/app/config unchanged.
