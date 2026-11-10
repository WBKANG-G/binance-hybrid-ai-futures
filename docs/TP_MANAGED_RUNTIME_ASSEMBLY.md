# Managed runtime assembly — Phase 4 synthetic candidate

The current assembly additionally requires an independent checkpoint provider and
a dedicated engine pool, plus an explicitly bound account configuration; see
`TP_SHARED_WEIGHT_CHECKPOINTS.md` and `TP_RUNTIME_STORAGE_BINDING.md`. Startup
and per-request wiring are additionally pinned by `TP_RUNTIME_START_GUARDS.md`.

This adds one explicit owner for the existing order journal, shared request-weight
budget, cloned client, and original coordinator. It does not activate trading,
provision storage, acquire/release a leader, or replace the installed CLI.

`assemble_managed_runtime` defaults to refusing execution before database,
filesystem, session, or HTTP access. Only an explicit synthetic-model opt-in can
construct a session. LIVE context, the standard network sender, enabled source
clients, and scopes other than isolated BTCUSDT DEMO are refused. A supplied
custom Python transport is trusted test-harness code, not an offline sandbox or
proof that arbitrary callables cannot access a network. There is no activation
CLI or environment switch.

## Assembly checks and ownership

The caller supplies the already acquired fenced lease, disabled source client,
raw transport, and existing storage configuration. The caller continues to own
that lease, source client, transport/pool, and engine. The assembly owns its cloned
client and exclusive order-journal handle. Closing the assembly does not release
somebody else's lease or close a shared transport.

Before a writable session is created, the factory reads the current lease and
ledger in a PostgreSQL read-only transaction, verifies the immutable client/ledger
scope, paused NORMAL/flat state, current period keys and bounded history profile,
and reads the existing order journal in read-only mode. Missing bindings/files,
wrong UUIDs, unresolved flows, invalid references, and busy locks block assembly.
No automatic bootstrap or repair occurs.

The shared request-weight journal now also supports a read-only mode: mode=ro,
query_only=ON, existing lock file, shared nonblocking file lock, and no pruning,
clock update, or persistent stop mutation. Expired completed rows are excluded
from the view without deleting them. Mutator methods reject read-only handles.
A startup hold or any pending shared request blocks construction.

The order journal is then opened exclusively and revalidated, shared pending
requests are checked again, and the original fenced bridge starts a new writer
session. The original managed-order budget and shared-pressure wrapper are
constructed together; the owned client is wired to that budget. Constructor
failure closes the owned order handle, preserving already committed metadata.
A session record is not erased if a later constructor fails.

These are local checks rather than a global transaction over both files and
PostgreSQL. Other cooperating accounts can add shared requests after a snapshot;
per-request shared reservations and original fenced effects remain mandatory.

## Run and close

A session can run once. It waits for the existing writer restart hold, rechecks
its process lock, current lease, journal bindings, period keys, and shared pending
requests before enabling its synthetic cloned client. It invokes the unchanged
managed candidate runner and original entry/protection/cleanup/full-flat proof.
On success, exception, or cancellation the cloned client is disabled again. A
failed or canceled run cannot be automatically repeated using the same session.

Closing while run is active is refused. Normal close revokes the owned budget
before releasing the journal handle. Later requests and proof checks fail before
HTTP or a new shared reservation. Close is idempotent; failure of local handle
cleanup keeps execution revoked but allows another local cleanup attempt.

Closing never seals a flow, refunds a role, clears a pending request, resets a
baseline, acknowledges an incident, or decides exchange exposure is flat. Those
decisions stay in the original safety path. Unfinished reservations survive close
and block the next assembly until their existing review/reconciliation procedure
has genuinely resolved them. A forged result string cannot release quota.

## Validation and limits

Tests cover default refusal before state access, missing/mismatched storage,
pending requests/flows, live context, stale and lost leases, period changes,
constructor cleanup, canceled/single-use runs, close retry, and post-close
transport rejection. DB account/event/outbox snapshots and complete file hashes
prove rejected startup paths do not change state. Read-only shared views preserve
expired rows and clock faults without writing.

Two complete assembled lifecycles use fake exchange HTTP, local WebSockets,
isolated PostgreSQL, ten-page history, and the original flat proof: TP fill and
partial fill followed by risk-reducing exit. Startup clocks are accelerated and
observation is short; request-weight timing remains real. They are not new
300-second natural observations or exchange trials.

Account-wide ORDERS usage, external/shared-IP traffic, endpoint cost upper bounds,
and coordinated rollback of both stores remain unresolved. Independent single-store
rollback checks are described in `TP_SHARED_WEIGHT_CHECKPOINTS.md`. Existing
source defaults, financial models, SDK, coordinator, risk policy, and installed
Windows state stay unchanged. There is no Windows deployment, actual exchange
request, or actual order from this development work. The review ZIP requires no
new Windows probe or trading command.
