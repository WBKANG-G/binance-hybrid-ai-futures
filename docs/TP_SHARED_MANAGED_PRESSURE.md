# Shared managed request-weight window — Phase 4 candidate

The existing per-process rolling gate cannot add requests made by another
process. This candidate adds a separate SQLite resource journal and an opt-in
`SharedManagedOrderWindowBudget`. The original SDK, managed order quota,
coordinator, command-line entry point, financial models, and specification remain
unchanged. No production caller is switched to this class and default real
execution remains disabled.

## Scope

All cooperating writers must use the same private local Linux directory, DEMO
scope label, expected journal UUID, policy, and host boot. The label is configured;
it is not discovery or proof of an actual egress IP. Other applications, other
keys, other directories/hosts, direct SDK calls, and outside users are not covered.
Actual account ORDERS usage and actual IP capacity are still unknown/unreserved.

Storage must remain intact: simultaneous administrative restores, valid old
backups, or coordinated rollback of the resource file are not supported or
independently detected by a fresh process. This candidate does not add an
external anti-rollback anchor for the shared resource file. It must not be
presented as production-wide capacity admission.

## Admission and persistence

The shared modeled ceiling is 5,700 (6,000 minus 300 headroom). Each request first
commits its positive modeled weight with SQLite FULL synchronization in a short
exclusive file-lock transaction, before any possible transport call. Other
processes see the committed reservation. A new scope waits 70.001 seconds; later
processes share its existing clock and pending records without resetting them.

A pending request never expires, even after process death or hours. A known
completed request is retained for 60.001 seconds **after completion**, covering
arbitrarily delayed sends that would be missed by timing only the earlier intent.
Only expired completed resource rows are pruned. The total reservation counter
never decreases. This table is bounded sliding-window resource metadata, not the
immutable financial ledger or a complete request audit log.

The dedicated files require mode 0600, a mode-0700 owner directory, stable regular
single-link identities, and an expected UUID/policy binding. Opening cannot create
missing storage. Boot changes, clock divergence/backward time, malformed persisted
accounting, missing files, or I/O errors prevent admission. No reset/repair or
provisioning CLI is added. The implementation is Linux/local-filesystem specific.

Response header floors supplement local charges. For an observed header from
request A, the floor includes all reservations made after A and all requests still
pending before A was reserved. Later reservations increase that floor. Keeping
each floor for a full window handles lower and out-of-order headers without
refunds; the maximum of local charges and all live floors controls admission.
This is deliberately conservative and can wait longer than a server minute reset.

Missing/invalid weight metadata and HTTP 418/429 persist a shared stopped flag.
Transport exceptions leave their reservation pending and attempt to persist the
same flag. Failure to record a stop (for example, unavailable storage) cannot be
assumed to notify other processes, but the already committed pending charge is
not returned. There is no automatic retry or refund for unknown outcomes.

## Original controls and request priorities

The wrapper reserves before invoking the original managed-order and rolling gates.
A later proven local refusal retains the shared charge for a full window but does
not invent a server response or poison transport trust. The raw sender requires
an internal reservation context and can be invoked only once per context.

Ordinary requests preserve 640 units, WATCH preserves 512, PROOF preserves 128,
and RESCUE can use the ceiling. The original route, ownership, fenced intent,
quantity, and reduce-only checks still apply; a lane name alone cannot bypass
them. Original cleanup clock and listen-key close requests receive rescue
priority. The complete original proof cost is checked against shared pressure
before starting proof; each individual request still reserves atomically.

A different writer can spend capacity after the proof precheck. Mid-proof local
capacity exhaustion therefore defers proof rather than accepting a partial proof.
No safety deadline or full-flat acceptance criterion is relaxed.

## Validation and delivery

Tests use real local disk/process locks, eight simultaneous processes, SIGKILL,
injected startup clocks, synthetic HTTP, and isolated PostgreSQL schemas. They
cover shared capacity and priority reserves, delayed completion, unknown outcomes,
out-of-order headers, sticky clock/boot faults, malformed metadata, non-mutating
local denials, and the original default execution refusal. Integrated partial-fill
and journal-failure lifecycles retain the original cleanup and flat checks.

The short lifecycle checks accelerate startup holds and shorten observation;
their request-weight clocks remain real. They are not new 300-second natural
observations or actual exchange trials. The delivery contains source, JUnit,
static checks, and synthetic traces. Windows installation, Windows DB changes,
exchange requests, and actual orders from this work are zero. No new Windows
probe or trading command is required by this review package.
