# Independent checkpoints for the shared weight window — Phase 4 candidate

The earlier shared resource file had no independent rollback reference. A fresh
process could accept a valid old copy with the same UUID and lose reservations.
`AnchoredSharedWeightJournal` now compares the entire logical SQLite state with
an independently committed, append-only PostgreSQL checkpoint before any update,
pruning, or new reservation. The managed runtime assembly requires this provider;
missing checkpoint configuration is refused before state access. The current
provider also uses an immutable installation identity and account configuration
binding; see `TP_RUNTIME_STORAGE_BINDING.md`.

## Bound state and locking

An explicit bootstrap creates a new shared file and binds its scope, journal UUID,
and policy to two auxiliary PostgreSQL tables. A checkpoint contains a monotonic
revision, deterministic immutable event ID, and SHA-256 of SQLite schema, metadata,
and all attempt rows. Header floors, pending/completed state, stopped flags,
clocks, and total reservations are covered. PostgreSQL triggers reject UPDATE and
DELETE of both binding and checkpoint records. Missing/gapped history blocks use.

Every cooperating writer acquires a PostgreSQL scope advisory lock, then the
SQLite file lock. Before SQLite can mutate, its fingerprint must match the latest
bound checkpoint. The new fingerprint is staged in the PostgreSQL transaction,
SQLite commits first, and PostgreSQL commits second. A reservation cannot return
a transport token until both commits finish. Scope locks are held across that
commit boundary, not across HTTP.

If SQLite commits but PostgreSQL fails, the next operation observes a mismatch
and stops. It never adopts the newer file as proof of free capacity. A response
checkpoint failure likewise blocks future usage, even if SQLite marked a request
complete. A transport failure's pending charge is preserved. A clock fault may
commit a stopped SQLite flag and leave a deliberately unmatched checkpoint; this
also requires review rather than automatic repair.

Read-only inspection uses a PostgreSQL read-only transaction with a shared scope
advisory lock and the existing SQLite read-only/shared-lock path. It cannot append
checkpoints, prune expired rows, advance clocks, or initialize missing data. All
cooperating writers use the matching exclusive advisory lock.

## Independent commit domain and lifecycle integration

The caller supplies a dedicated checkpoint engine/pool, separate from the account
ledger's pool. Runtime assembly checks pool identity. This prevents a fenced
account effect from consuming every connection while trying to independently
commit its resource reservation. The checkpoint engine remains caller-owned;
closing a runtime never disposes somebody else's engine or releases their lease.

Opening, running, or reviewing never creates auxiliary tables or binds an old
file. Schema provisioning and bootstrap are separate explicit APIs, used only in
isolated cloud tests here. An existing unbound file cannot be adopted; bootstrap
uses exclusive file creation and refuses it. No repair, reset, credit restoration,
installation, migration, or trading-enable command is added.

The original rolling ceiling, priority reserves, full-flow order quota, fencing,
position/quantity limits, and full flat proof remain mandatory. No deadline or
flat acceptance test is relaxed. The canonical production CLI and its disabled
execution default are unchanged.

## Validation and practical limits

Tests restore valid old SQLite files (not damaged hashes), including same-UUID,
same-inode copies that discard reservations, completion state, or a stopped flag.
They also restore the PostgreSQL head independently and remove history records.
All such mismatches block use without changing either store during diagnosis.
Commit-failure tests cover both pre-send reservation and post-response completion.
A real child process is SIGKILLed after SQLite commit but before PostgreSQL commit;
the pending 5,700 charge survives and the next process refuses the mismatch.
Eight simultaneous processes share the checkpoint and cannot each spend the same
budget. Account-effect rollback cannot undo independently committed reservations.

End-to-end tests exercise the required provider through runtime assembly, TP fill,
and partial-fill risk-reducing exit, using synthetic exchange HTTP, local WSS,
isolated PostgreSQL schemas, accelerated startup clocks, and short observation.
They are not new 300-second natural observations or exchange trials. Docker smoke
runs without a network. There are no Windows DB changes, actual exchange requests,
actual orders, or deployments from this development work.

This detects a rollback of either store while its counterpart is intact. A
coordinated restore of both stores to matching old state is not independently
detectable here. This is not protection against a hostile database administrator
or compromised application host. PostgreSQL must be authoritative for the
configured scope; a stale replica or a different checkpoint database is not an
approved substitute. Backups and provisioning remain deployment work.

All cooperating writers must use the checkpointed provider and the same scope.
The legacy unanchored base journal remains for isolated window tests; it provides
no such rollback guarantee. Unmanaged writers can still bypass the prototype.
Account-wide ORDERS usage, external/shared-IP capacity, and complete endpoint cost
bounds remain unproven, so actual execution stays disabled. This delivery is a
source review package and requires no additional command on the current Windows PC.
