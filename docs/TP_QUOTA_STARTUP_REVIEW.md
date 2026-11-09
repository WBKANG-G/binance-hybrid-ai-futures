# Read-only managed quota startup review

This Phase 4 change adds a local startup check without constructing an execution
bridge, acquiring a leader lease, starting a journal session, or accessing exchange
credentials. It does not bootstrap storage, repair records, reconcile exchange
orders, release quota, roll a baseline, or resume trading.

Previously, opening a writable journal could create a missing lock file, and
constructing `ManagedQuotaBridge` recorded a new session. Those APIs remain writer
APIs. The new path uses `DurableOrderQuota(..., read_only=True)` and the same pure
binding validator used by the writer.

The SQLite connection uses `mode=ro` plus `query_only=ON`. Its existing lock file
is opened without `O_CREAT` and held with a shared nonblocking lock. Readers can
coexist; a writer and a reader exclude each other. A read-only handle cannot be
used as the writer lock required by the emergency exit fallback. Opening missing
storage never initializes it. Append and session-start methods reject read-only
handles, and SQLite itself rejects DML.

The PostgreSQL reader explicitly uses `REPEATABLE READ, READ ONLY`, with bounded
statement and lock timeouts. It reads account state and the existing lease from
one snapshot. It does not invoke `DemoStore.acquire`, `change`, or `release`.
An integration test injects an accidental UPDATE into this transaction and checks
that PostgreSQL rejects it and leaves all account, event, and outbox rows equal.

## Results

- Missing binding: `STORAGE_NOT_PROVISIONED`, without accessing the directory.
- Missing storage, invalid identity/chain, or PostgreSQL/journal disagreement:
  blocked, without initialization or repair.
- Held writer lock: busy, without starting another session.
- Valid storage but live lease, unpaused/non-NORMAL state, local exposure,
  mismatched period keys, or a pending flow: blocked with the corresponding local
  reason. Pending reservations and claims remain intact.
- All local checks passed: `LOCAL_CHECKS_PASSED_KEEP_PAUSED`. This is a snapshot
  review result only. Both local window counters stay null because no writer
  session or restart hold has run. External account usage stays UNKNOWN.

Every result keeps execution, orders, and live trading disabled. The assessment
does not include raw request IDs, database URLs, private paths, exchange keys,
financial values, or unsanitized exceptions. Historical unresolved request counts
are reported separately; this check does not rewrite prior incident history.

`scripts/review_demo_quota_startup.py` is the local read-only entry point. It has
no apply, bootstrap, clear, recovery, or trading-enable option. LIVE context and
non-DEMO account names are rejected before database access. A zero exit status
means only that local checks passed, never trading readiness. No command needs
to be run on the current Windows installation as part of this review delivery;
the new storage has not been deployed there.

## Limits and validation

The existing named-ledger/shared-directory scope, local Linux locking assumption,
default policy, and 70.001-second writer restart hold still apply. A clean local
snapshot can become stale after its locks are released; it cannot authorize a
later execution. Only the original fenced runtime and complete exchange proof
can govern a later flow. Shared-IP pressure, actual account ORDERS usage, and
endpoint cost upper bounds remain unresolved for production admission.

Validation includes byte-for-byte journal comparisons, complete PostgreSQL
account/event/outbox comparisons, busy-lock and missing-file cases, preserved
pending claims, CLI error redaction and early refusal, existing safety regression,
and synthetic partial-fill/storage-failure lifecycle checks. Docker smoke runs
without a network. There are no actual exchange requests or Windows DB changes.
