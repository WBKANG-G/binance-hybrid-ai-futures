# Runtime startup and client wiring guards — Phase 4 candidate

Two regressions were reproduced on parent
`b23d47d9157e1161321b36212605ead2b2b3b917`: after successful assembly, changing
the LIVE context or replacing `client.send` did not cause an immediate startup
refusal. The synthetic sender was reached and startup failed later on its dummy
clock response. These were early-query/context gaps; the reproductions did not
submit an entry or use the real exchange.

## Fresh startup checks

The runtime checks its startup environment and captured wiring before waiting for
the writer hold, then repeats the checks after the wait. A fresh fenced read-only
ledger snapshot must still have the accepted immutable policy/symbol/margin
scope, paused NORMAL risk state, no local exposure/pending order, the expected
history profile and current period keys. Storage bindings, file/process ownership
and unresolved flow/request checks remain mandatory.

The owned coordinator, client, budget, bridge, store and lease must still match
the assembled context. Client credentials and disabled settings are pinned.
Budget transport/bridge identity, explicit synthetic mode, route costs and model
profile are pinned too. A pre-used, admitted, stopped or revoked budget cannot be
started as a fresh session. No reset or automatic retry is introduced.

Shared pending-state inspection now uses a separate read-only checkpointed view.
It does not prune rows, update clocks or append a checkpoint when startup is
refused. Known invalid startup state is rejected before lease-renewal heartbeats.
Changes during the hold are rejected by the second snapshot.

## Per-request wiring

`RuntimeBoundClient` extends the original uncertain-cancellation client and checks
its transport, credential object and normalized immutable settings before public,
signed and listen-key requests. A sender replacement cannot silently bypass the
owned budget after startup. Normal internal toggling of DEMO enablement remains
allowed; policy and market scope do not change with that toggle.

The owned budget checks its configured sender, raw sender, bridge, shared provider,
model and costs before any new resource reservation. These checks supplement the
original SDK and fenced intent/ownership/quantity checks, not replace them.

The startup environment check is not a newly added mid-position kill switch.
Existing risk-reducing cleanup keeps its original behavior, scope and acceptance
rules. No cleanup deadline or full-flat proof is relaxed.

## Failure and resource ownership

Success, failure and cancellation restore the factory-captured disabled settings,
rather than retaining a changed scope. A runtime remains single-use after a local
startup refusal. Close revokes and closes only its captured owned budget/journal,
even if public aliases were changed. It cannot revoke an unrelated replacement
object. Pending flow records and reservations are never refunded or sealed by
startup refusal or close.

A snapshot can become stale after its transaction ends; this is not a global
transaction freezing the world. The original fenced effect and account risk checks
remain authoritative at submission. Arbitrary hostile Python code/private-state
rewrites, unmanaged writers and coordinated matching store restores remain outside
this candidate's guarantee.

## Validation and delivery

Tests reproduce the two prior late failures, then verify refusal before sender
calls and persistent-state changes. They cover risk/pause/exposure/history drift,
changes during the wait, credentials/settings and coordinator/budget/journal alias
changes, per-request public/signed/listen-key sender replacement, budget/model
changes, and pending shared work. DB account/event/outbox rows and complete file
hashes are compared. The original default execution refusal is unchanged.

Full regression and assembled TP/partial-fill lifecycles use synthetic HTTP,
local WSS and isolated PostgreSQL. Startup holds are accelerated; observation is
short and request-weight timing stays real. These are not new 300-second natural
observations or actual exchange trials. Network-free Docker smoke checks the
same import/default-refusal/client-wiring code; the complete PostgreSQL protocol
is verified by separate integration tests.

The canonical installed CLI, locked specification, financial models and risk
parameters remain unchanged. No Windows install, actual exchange request, actual
order or trading resume occurs. Account ORDERS usage, external IP capacity and
request cost upper bounds remain unproven; real execution stays disabled. This
review delivery requires no additional Windows query or trading command.
