# Account binding for the runtime pressure provider — Phase 4 candidate

A regression test reproduced a configuration bypass in parent commit
`79685d7217811a7674742c2bd07ddbc7ddbebf96`: with pending weight in the original
scope, assembly accepted another valid, empty, warmed-up scope. Both stores were
internally consistent, so checkpoint comparison alone did not catch the switch.
The test failed because the expected startup refusal did not occur. No HTTP was
needed to reproduce it.

## Account configuration pin

The DEMO ledger now carries `MANAGED_RUNTIME:SHARED_STORAGE_V1`, containing the
scope, journal UUID, checkpoint installation UUID, policy digest and version.
Factory assembly compares that marker before opening writable runtime resources.
Missing or changed configuration is refused; assembly never creates a marker.
The session pins the accepted value and rechecks it before running. Removing or
changing the marker after construction stops run before HTTP.

`bind_runtime_storage` is a separate, explicit metadata write API. It requires a
current fenced DEMO lease, disabled LIVE context, paused NORMAL/flat state, current
period keys, existing matching order journal, no unfinished order flow, warmed-up
checkpointed pressure storage, and no pending shared request. It holds a read-only
order-journal handle while publishing the marker through the original fenced
ledger change. Neither file is initialized, reset, pruned or repaired.

An identical existing binding is idempotent without another audit event. A
different existing binding is rejected, even if the proposed alternative store
is valid. An idempotent return certifies configuration identity, not store health
or trading readiness; ordinary runtime checks still run. No rebind, clear,
migration or activation CLI is provided.

This marker is protected by the configuration API and fenced ledger ownership,
not by a new PostgreSQL trigger over arbitrary JSON account operations. Direct
administrative edits or other trusted code that deliberately rewrites that JSON
are not an approved reconfiguration path. Restored/unbound account metadata
requires explicit provisioning review; runtime does not infer or adopt a store.

## Checkpoint installation identity

Explicit checkpoint provisioning creates one installation UUID in an auxiliary
identity table. PostgreSQL triggers reject UPDATE/DELETE of that identity, as for
existing binding/history tables. Every checkpoint transaction checks it. A live
anchor object remembers its first accepted identity and refuses a silent backend
switch, even before SQLite fingerprint checking.

The account marker pins that installation UUID for new processes as well. A
fresh backend with the same scope and journal UUID therefore cannot substitute
for the configured authority. Credentials, URLs and private paths are not stored
in the marker. Moving an unchanged store between paths does not create capacity;
checkpoint comparison and ordinary file-identity checks still apply.

The owned budget also pins the configured provider object and its scope, UUID,
and anchor object. Replacement after assembly is rejected before creating a new
shared reservation or invoking transport. This is an accidental-configuration
and lifecycle guard for trusted Python callers, not a sandbox against hostile
code modifying all private state.

## Provisioning and validation

The auxiliary identity table is created only by explicit provisioning. No automatic
schema update is added for earlier prototype checkpoint schemas. An old schema
without identity metadata is refused; deployment/migration remains separate work.
The canonical CLI, locked specification, risk policy, sizing, full flat proof,
financial baselines, and default real-execution refusal remain unchanged.

Tests verify the reproduced alternate-scope bypass, same-UUID alternate backend,
installation switches, DB-enforced installation immutability, explicit binding
idempotence, refusal to overwrite existing configuration, missing/deleted account
markers, pending work, wrong leases, period/LIVE context, and provider replacement.
Rejected paths compare account/event/outbox rows and file hashes before/after.
Full regression and original TP/partial-fill lifecycle tests use explicit setup
bindings, fake exchange HTTP, local WSS and isolated PostgreSQL schemas. Startup
holds are accelerated and observation is short; request-weight timing remains real.

Checkpoint rollback protection still assumes its counterpart stays intact.
Coordinated matching restores and hostile administrators/hosts are not covered.
Actual account ORDERS usage, outside/shared-IP traffic and route-cost bounds are
still unproven. No Windows install, production migration, actual exchange request,
actual order or trading resume is performed. The review package requires no new
Windows command.
