# Validation

2026-11-03 cloud Python 3.12, dedicated synthetic PostgreSQL on localhost:55435.

7 scoped tests passed in 2.40s: PostgreSQL transaction_read_only on; all financial rows/events/outbox unchanged; exactly one signed historical-order GET; local-only no-network path; mismatched identity, missing timestamp, reversed timestamp, exception redaction; write-enabled client never queried. Ruff check/format and mypy --strict passed. Tests use synthetic transport, no Binance account or actual exchange requests.

Initial test setup failed on missing strategy=None, explicit isolated minimum cap and timestamped quote fixture. Fixture was corrected to valid existing contracts; assertions were not removed or weakened.

Windows execution of this diagnostic remains pending. User's prior TP observation reported NOT_ACCEPTED/TP_FILL_NOT_OBSERVED, entry CANCELED/zero fill, VERIFIED_FLAT/NORMAL/paused; these are historical user evidence, not results of the synthetic tests.
