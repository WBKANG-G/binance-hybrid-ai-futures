# Durable managed-writer quota — Phase 4 review only

This adds an opt-in coordinator and budget subclass, not a trading activation or
Windows installation. Existing production files, the canonical CLI, financial
models, migrations, and locked specification are unchanged. Real execution stays
disabled by default. No automatic recovery or activation CLI is provided.

## Problem and scope

The DEMO review returned ORDERS limits of 300/10 seconds and 1,200/minute but did
not return usage counters. Missing usage remains UNKNOWN. This implementation
does not turn a local journal, a quiet period, or a successful synthetic test into
an account-wide capacity reservation.

The scope is cooperating writers for one named DEMO ledger using the same private
Linux directory and PostgreSQL ledger. Other keys, account aliases, unmanaged
scripts, and other users sharing an IP are outside this scope. Correct local
filesystem locking and fsync semantics are required; network filesystems and
native Windows execution have not been qualified. The intended packaging context
is Linux inside Docker, but no deployed volume has been provisioned here.

## Conservative local accounting

- Local model caps are six slots/10 seconds and twelve slots/60 seconds.
- A flow reserves all six before entry: one entry, two protective creates, two
  possible algo trigger executions, and one risk-reducing exit.
- All six remain held for the entire pending flow, even after minutes. An early
  intent timestamp cannot expire a later send or algo trigger.
- Only the original complete flat confirmation can seal a flow. Sealing charges
  all six at closure, including unused slots. It never refunds an individual send.
- Every session has a 70.001-second monotonic hold. Local window counts are null
  during this hold; retained modeled charges are still reported. Old pending
  flows remain blocked after the hold. External account usage remains UNKNOWN.
- UTC/monotonic divergence, damaged storage, binding mismatch, and journal limits
  block new admission. There is no automatic reset, repair, rotation, compaction,
  or credit restoration. The journal is capped at 10,000 events and 16 MiB.

## Durable send boundary

`packages/persistence/demo_order_quota.py` uses a separate FULL-synchronous SQLite
journal with immutable, hash-chained events. A nonblocking process lock remains
held across network work. Files require mode 0600, the directory mode 0700, the
same owner, regular single-link files, and stable inode identities.

`services/execution/demo_managed_order_quota.py` binds the journal UUID, policy,
reservations, and seals to immutable references in the original fenced
PostgreSQL DEMO ledger. Explicit bootstrap requires a locally paused, NORMAL,
flat ledger without an existing binding. Opening never creates missing storage.
Missing files, a valid but rolled-back file, orphan reservations, or a seal whose
PostgreSQL commit failed block later admission.

Each possible POST first consumes its deterministic role in a separate SQLite
commit inside the original fenced effect context, before the existing weight
gate and transport. A subsequent local denial, unknown response, process death,
or PostgreSQL rollback cannot refund that role. The journal is auxiliary
execution metadata, not financial truth or an exchange usage counter.

If storage fails during an already reserved flow, a single original owned
reduce-only exit can use an in-process fallback only with its existing fenced
SUBMITTING intent, positive remaining position, six-slot PostgreSQL anchor, and
still-owned process lock. The existing SDK, ownership/quantity checks, and weight
gate still apply. No entry fallback or automatic unknown-exit retry is added.
Losing the process also loses this fallback; unresolved recovery requires review.

## Preserved controls and remaining work

The original sizing, reduce-only cap, lease fencing, request idempotency, full
flat proof, financial history, and rolling REQUEST_WEIGHT gate remain in force.
The added coordinator runs through those original paths in synthetic tests.
Local storage denials happen before transport; they are not fabricated server
responses and do not poison the existing rescue transport trust.

This closes local concurrent-writer and restart/rollback accounting gaps. It does
not prove account-wide ORDERS usage, algo counter upper bounds, shared-IP
capacity, complete endpoint cost bounds, or production admission. Existing actual
Windows data, its paused state, and all day/week baselines are untouched. Further
deployment and actual execution remain separate work; do not run this review
package as an installer or resume trading from its results.

## Validation

Validation uses fake exchange HTTP, local WebSockets, isolated PostgreSQL schemas,
real local file/process locks, and real SIGKILL. Two final lifecycle cases use
the actual 70.001-second startup hold and 300-second observation. Partial fill,
journal failure, and cancellation failure cases accelerate only the startup quota
clock and use shorter observation; the request-weight clock stays real.

The accompanying review artifact contains JUnit, static-check output, complete
synthetic traces, and independent 60.001-second request-weight measurements.
Actual exchange requests and actual Windows database changes from this work: zero.
