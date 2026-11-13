"""Read-only comparison to a separately supplied digest, never restore authority.

Reference creation cannot establish external custody or latestness. No backup,
restore, repair, writer reopening, credential resumption, or network calls here.
"""

import hashlib
import os
import time
from datetime import UTC
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import Text, func, select, text
from sqlalchemy.engine import Connection

from packages.persistence.demo import accounts, events, outbox
from packages.persistence.demo_read_audit import digest, require
from packages.persistence.demo_read_recovery import (
    _file,
    _private_directory,
    _sync_directory,
    _write_new,
)
from packages.persistence.demo_response_evidence import _line
from packages.persistence.demo_shared_weight import SharedWeightJournal
from packages.persistence.demo_wire_checkpoint_candidate import POLICY as ANCHOR_POLICY
from packages.persistence.demo_wire_checkpoint_candidate import WireCheckpointStore
from services.execution.demo_request_outcome_candidate import _json
from services.execution.demo_wire_session_contract_candidate import (
    MAX_BYTES,
    MAX_RECORDS,
    _hash,
    _replay,
    _scope,
    _uuid,
)

POLICY = "SYNTHETIC_WIRE_RESTORE_REFERENCE_CANDIDATE_V1"
MAX_MANIFEST = 8192
MAX_HISTORY_ROWS = 4096
MAX_HISTORY_BYTES = 512 * 1024 * 1024
MAX_HISTORY_ROW_BYTES = 2 * 1024 * 1024
MAX_ACCOUNT_BYTES = 2 * 1024 * 1024
HISTORY_PAGE_ROWS = 64
MAX_HISTORY_SECONDS = 10
UNRESOLVED = {"UNKNOWN", "SUBMITTING", "CANCEL_REQUESTED", "NOT_FOUND_UNRESOLVED"}
FIELDS = {
    "binding": {"scope", "account", "journal", "installation", "binding_hash", "wire_root_hash"},
    "checkpoint": {"sequence", "checkpoint_hash", "wire_hash"},
    "files": {"wire_bytes", "wire_sha256", "witness_bytes", "witness_sha256"},
    "financial": {
        "state_sha256",
        "account_row_sha256",
        "history_catalog_sha256",
        "history_rows",
        "history_bytes",
        "history_max_row_bytes",
    },
    "state": {
        "records",
        "sessions",
        "operations",
        "financial_prepares",
        "pending_operations",
        "uncertain_outcome",
        "paused",
        "unresolved_local_requests",
    },
}


def _integer(value: Any, minimum: int, maximum: int) -> None:
    require(type(value) is int and minimum <= value <= maximum, "WIRE_REFERENCE_INTEGER_INVALID")


def _validate(value: Any) -> None:
    require(
        isinstance(value, dict)
        and set(value) == {"policy", "anchor_policy", "environment", *FIELDS}
        and value["policy"] == POLICY
        and value["anchor_policy"] == ANCHOR_POLICY
        and value["environment"] == "SYNTHETIC_ONLY",
        "WIRE_REFERENCE_SCHEMA_INVALID",
    )
    for group, keys in FIELDS.items():
        require(
            isinstance(value[group], dict) and set(value[group]) == keys,
            "WIRE_REFERENCE_SCHEMA_INVALID",
        )
    binding, tip, files, finance, state = (value[k] for k in FIELDS)
    _scope(binding["account"], binding["scope"])
    for name in ("journal", "installation"):
        _uuid(binding[name])
    for container, names in (
        (binding, ("binding_hash", "wire_root_hash")),
        (tip, ("checkpoint_hash", "wire_hash")),
        (files, ("wire_sha256", "witness_sha256")),
        (finance, ("state_sha256", "account_row_sha256", "history_catalog_sha256")),
    ):
        for name in names:
            _hash(container[name])
    _integer(tip["sequence"], 0, MAX_RECORDS - 1)
    for name in ("wire_bytes", "witness_bytes"):
        _integer(files[name], 1, MAX_BYTES)
    for name in ("records", "sessions", "operations", "financial_prepares", "pending_operations"):
        _integer(state[name], 1 if name == "records" else 0, MAX_RECORDS)
    _integer(state["unresolved_local_requests"], 0, 768)
    require(
        type(state["uncertain_outcome"]) is bool
        and type(state["paused"]) is bool
        and state["records"] == tip["sequence"] + 1
        and state["sessions"] <= state["records"]
        and state["financial_prepares"] <= state["operations"] <= state["records"]
        and state["pending_operations"] <= state["operations"]
        and (not state["uncertain_outcome"] or state["pending_operations"] > 0),
        "WIRE_REFERENCE_COUNTS_INVALID",
    )
    _integer(finance["history_rows"], 0, MAX_HISTORY_ROWS)
    _integer(finance["history_bytes"], 0, MAX_HISTORY_BYTES)
    _integer(finance["history_max_row_bytes"], 0, MAX_HISTORY_ROW_BYTES)
    require(
        (finance["history_rows"] == 0)
        == (finance["history_bytes"] == finance["history_max_row_bytes"] == 0)
        and finance["history_max_row_bytes"]
        <= finance["history_bytes"]
        <= finance["history_rows"] * finance["history_max_row_bytes"],
        "WIRE_REFERENCE_HISTORY_COUNTS_INVALID",
    )


def parse_reference(body: bytes) -> dict[str, Any]:
    require(isinstance(body, bytes) and 0 < len(body) <= MAX_MANIFEST, "WIRE_REFERENCE_BODY_LIMIT")
    value = _json(body)
    _validate(value)
    require(_line(value) == body, "WIRE_REFERENCE_CANONICAL_BYTES_REQUIRED")
    return value


def _history(c: Connection, account: str) -> dict[str, Any]:
    size = func.octet_length(events.c.payload)
    count, total, largest = c.execute(
        select(
            func.count(), func.coalesce(func.sum(size), 0), func.coalesce(func.max(size), 0)
        ).where(events.c.account_id == account)
    ).one()
    require(
        type(count) is int
        and type(total) is int
        and type(largest) is int
        and 0 <= count <= MAX_HISTORY_ROWS
        and 0 <= total <= MAX_HISTORY_BYTES
        and 0 <= largest <= MAX_HISTORY_ROW_BYTES,
        "WIRE_REFERENCE_HISTORY_PREFLIGHT_LIMIT",
    )
    catalog: list[dict[str, Any]] = []
    started = time.monotonic()
    cursor = ""
    observed_bytes = 0
    while len(catalog) < count:
        require(
            0 <= time.monotonic() - started <= MAX_HISTORY_SECONDS,
            "WIRE_REFERENCE_HISTORY_TIME_BUDGET",
        )
        page = (
            select(
                events.c.event_id, events.c.fence, events.c.as_of, events.c.kind, events.c.payload
            )
            .where(events.c.account_id == account, events.c.event_id > cursor)
            .order_by(events.c.event_id)
            .limit(HISTORY_PAGE_ROWS)
            .subquery()
        )
        rows = (
            c.execute(
                select(
                    page.c.event_id,
                    page.c.fence,
                    page.c.as_of,
                    page.c.kind,
                    func.octet_length(page.c.payload).label("body_bytes"),
                    func.encode(func.sha256(func.convert_to(page.c.payload, "UTF8")), "hex").label(
                        "body_sha256"
                    ),
                    outbox.c.topic,
                )
                .select_from(page.outerjoin(outbox, outbox.c.event_id == page.c.event_id))
                .order_by(page.c.event_id)
            )
            .mappings()
            .all()
        )
        require(
            bool(rows) and len(catalog) + len(rows) <= count,
            "WIRE_REFERENCE_HISTORY_COVERAGE_INVALID",
        )
        for row in rows:
            _uuid(row["event_id"])
            _hash(row["body_sha256"])
            require(
                row["event_id"] > cursor
                and row["topic"] == "DEMO_AUDIT"
                and type(row["fence"]) is int
                and row["fence"] > 0
                and isinstance(row["kind"], str)
                and 1 <= len(row["kind"]) <= 80,
                "WIRE_REFERENCE_HISTORY_METADATA_INVALID",
            )
            catalog.append(
                dict(
                    event_id=row["event_id"],
                    fence=row["fence"],
                    kind=row["kind"],
                    as_of=row["as_of"].astimezone(UTC).isoformat(),
                    body_bytes=row["body_bytes"],
                    body_sha256=row["body_sha256"],
                    topic=row["topic"],
                )
            )
            observed_bytes += row["body_bytes"]
            cursor = row["event_id"]
        require(
            0 <= time.monotonic() - started <= MAX_HISTORY_SECONDS,
            "WIRE_REFERENCE_HISTORY_TIME_BUDGET",
        )
    require(
        len(catalog) == count and observed_bytes == total, "WIRE_REFERENCE_HISTORY_COVERAGE_INVALID"
    )
    return dict(
        history_rows=count,
        history_bytes=total,
        history_max_row_bytes=largest,
        history_catalog_sha256=digest(catalog),
    )


def create_reference_manifest(
    store: WireCheckpointStore, directory: Path, scope: str, journal: UUID, account: str
) -> bytes:
    _scope(account, scope)
    require(isinstance(journal, UUID), "WIRE_REFERENCE_EXPECTED_JOURNAL_REQUIRED")
    _private_directory(directory)
    with (
        store.engine.connect().execution_options(isolation_level="REPEATABLE READ") as c,
        c.begin(),
    ):
        c.execute(text("SET TRANSACTION READ ONLY"))
        store._lock(c, scope, read_only=True)
        binding, rows, wire, witness = store._verify(c, directory, scope)
        require(
            binding["journal"] == str(journal) and binding["account"] == account,
            "WIRE_REFERENCE_EXPECTED_BINDING_MISMATCH",
        )
        state_bytes = c.execute(
            select(func.octet_length(accounts.c.state.cast(Text))).where(
                accounts.c.account_id == account
            )
        ).scalar_one()
        require(
            type(state_bytes) is int and 0 < state_bytes <= MAX_ACCOUNT_BYTES,
            "WIRE_REFERENCE_ACCOUNT_PREFLIGHT_LIMIT",
        )
        row = c.execute(select(accounts).where(accounts.c.account_id == account)).mappings().one()
        financial_state = row["state"]
        requests = financial_state.get("requests", {})
        require(
            isinstance(requests, dict)
            and len(requests) <= 768
            and all(isinstance(v, str) and len(v) <= 64 for v in requests.values())
            and type(financial_state.get("paused")) is bool,
            "WIRE_REFERENCE_ACCOUNT_STATE_INVALID",
        )
        account_metadata = dict(
            account=account,
            owner=row["owner"],
            fence=row["fence"],
            lease_until=None
            if row["lease_until"] is None
            else row["lease_until"].astimezone(UTC).isoformat(),
            state_sha256=digest(financial_state),
        )
        replay = _replay([r["wire"] for r in rows])
        result = dict(
            policy=POLICY,
            anchor_policy=ANCHOR_POLICY,
            environment="SYNTHETIC_ONLY",
            binding=dict(
                scope=scope,
                account=account,
                journal=str(journal),
                installation=binding["installation"],
                binding_hash=binding["hash"],
                wire_root_hash=binding["wire_root_hash"],
            ),
            checkpoint=dict(
                sequence=rows[-1]["sequence"],
                checkpoint_hash=rows[-1]["hash"],
                wire_hash=rows[-1]["wire"]["hash"],
            ),
            files=dict(
                wire_bytes=len(wire),
                wire_sha256=hashlib.sha256(wire).hexdigest(),
                witness_bytes=len(witness),
                witness_sha256=hashlib.sha256(witness).hexdigest(),
            ),
            financial=dict(
                state_sha256=digest(financial_state),
                account_row_sha256=digest(account_metadata),
                **_history(c, account),
            ),
            state=dict(
                records=len(rows),
                sessions=len(replay["sessions"]),
                operations=len(replay["operations"]),
                financial_prepares=sum(r["finance"] is not None for r in rows),
                pending_operations=sum(
                    op["phase"] != "OBSERVED" for op in replay["operations"].values()
                ),
                uncertain_outcome=replay["unknown"],
                paused=financial_state["paused"],
                unresolved_local_requests=sum(v in UNRESOLVED for v in requests.values()),
            ),
        )
        _validate(result)
        body = _line(result)
        require(len(body) <= MAX_MANIFEST, "WIRE_REFERENCE_BODY_LIMIT")
        return body


def review_restored_reference(
    store: WireCheckpointStore,
    directory: Path,
    scope: str,
    journal: UUID,
    account: str,
    reference: bytes,
    *,
    retained_sha256: str | None = None,
) -> dict[str, Any]:
    require(retained_sha256 is not None, "WIRE_REFERENCE_SEPARATELY_RETAINED_DIGEST_REQUIRED")
    _hash(retained_sha256)
    expected = parse_reference(reference)
    require(
        hashlib.sha256(reference).hexdigest() == retained_sha256,
        "WIRE_REFERENCE_RETAINED_DIGEST_MISMATCH",
    )
    require(
        expected["binding"]["scope"] == scope
        and expected["binding"]["journal"] == str(journal)
        and expected["binding"]["account"] == account,
        "WIRE_REFERENCE_EXPECTED_BINDING_MISMATCH",
    )
    current_body = create_reference_manifest(store, directory, scope, journal, account)
    current = parse_reference(current_body)
    differences = [
        group + "." + name
        for group, fields in FIELDS.items()
        for name in sorted(fields)
        if expected[group][name] != current[group][name]
    ]
    matches = not differences
    blockers = [
        "REFERENCE_EXTERNAL_CUSTODY_NOT_PROVEN",
        "LATEST_REFERENCE_NOT_PROVEN",
        "REMOTE_ACCOUNT_IDENTITY_NOT_VERIFIED",
        "FRESH_EXCHANGE_STATE_NOT_CHECKED",
        "NO_WRITER_RESTART_PROTOCOL",
    ]
    if not matches:
        blockers.insert(0, "SNAPSHOT_DIFFERS_FROM_RETAINED_REFERENCE")
    if current["state"]["pending_operations"] or current["state"]["uncertain_outcome"]:
        blockers.append("UNRESOLVED_DISPATCH_PRESENT")
    if current["state"]["unresolved_local_requests"]:
        blockers.append("LOCAL_REQUEST_OUTCOMES_UNRESOLVED")
    if not current["state"]["paused"]:
        blockers.append("ACCOUNT_NOT_PAUSED")
    return dict(
        restore_reference_review="MATCHED_SUPPLIED_REFERENCE_KEEP_PAUSED"
        if matches
        else "BLOCKED_REFERENCE_MISMATCH_KEEP_PAUSED",
        matches_supplied_retained_reference=matches,
        mismatch_fields=differences,
        supplied_reference_sha256=retained_sha256,
        current_reference_sha256=hashlib.sha256(current_body).hexdigest(),
        comparison_scope="ONE_ACCOUNT_AND_ONE_WIRE_JOURNAL_EXACT_SNAPSHOT_ONLY",
        history_catalog_complete_within_limits=True,
        historical_events_replayed=False,
        external_reference_authenticity_verified=False,
        reference_is_latest_proven=False,
        whole_system_rollback_protection_verified=False,
        whole_runtime_backup_verified=False,
        storage_restore_authorized=False,
        restore_performed=False,
        process_restart_resumption_supported=False,
        credential_session_resumed=False,
        remote_account_identity_verified=False,
        request_states_changed=False,
        database_changed=False,
        filesystem_changed=False,
        actual_http_attempts=0,
        order_writes=0,
        execution_approved=False,
        orders_enabled=False,
        live_trading_enabled=False,
        remaining_blockers=blockers,
    )


def _separate(directory: Path, source: Path) -> None:
    _private_directory(directory)
    require(
        directory.resolve() != source.resolve()
        and source.resolve() not in directory.resolve().parents,
        "WIRE_REFERENCE_SEPARATE_DIRECTORY_REQUIRED",
    )


def export_reference_copy(
    store: WireCheckpointStore,
    source: Path,
    destination: Path,
    scope: str,
    journal: UUID,
    account: str,
    *,
    apply: bool = False,
) -> dict[str, Any]:
    require(apply is True, "WIRE_REFERENCE_EXPLICIT_COPY_REQUIRED")
    _separate(destination, source)
    body = create_reference_manifest(store, source, scope, journal, account)
    reference_hash = hashlib.sha256(body).hexdigest()
    path = destination / (reference_hash + ".wire-reference.json")
    if path.exists() or path.is_symlink():
        require(_file(path, MAX_MANIFEST) == body, "WIRE_REFERENCE_EXISTING_COPY_MISMATCH")
        created = False
    else:
        _write_new(path, body)
        created = True
    identity = SharedWeightJournal._identity(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        require((info.st_dev, info.st_ino) == identity, "WIRE_REFERENCE_COPY_REPLACED")
        os.fsync(fd)
    finally:
        os.close(fd)
    _sync_directory(destination)
    require(_file(path, MAX_MANIFEST) == body, "WIRE_REFERENCE_COPY_WRITE_MISMATCH")
    return dict(
        reference_copy_saved=True,
        reference_copy_created=created,
        retained_sha256=reference_hash,
        filename=path.name,
        reference_external_custody_verified=False,
        reference_is_latest_proven=False,
        database_changed=False,
        filesystem_changed=created,
        restore_performed=False,
        execution_approved=False,
        orders_enabled=False,
        live_trading_enabled=False,
    )


def review_reference_file(
    store: WireCheckpointStore,
    source: Path,
    scope: str,
    journal: UUID,
    account: str,
    reference_file: Path,
    *,
    retained_sha256: str | None = None,
) -> dict[str, Any]:
    require(retained_sha256 is not None, "WIRE_REFERENCE_SEPARATELY_RETAINED_DIGEST_REQUIRED")
    _hash(retained_sha256)
    _separate(reference_file.parent, source)
    return review_restored_reference(
        store,
        source,
        scope,
        journal,
        account,
        _file(reference_file, MAX_MANIFEST),
        retained_sha256=retained_sha256,
    )
