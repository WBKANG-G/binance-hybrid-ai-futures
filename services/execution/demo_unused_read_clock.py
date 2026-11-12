"""Explicit pre-audit recovery, strictly limited to the existing unused journal."""

import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import inspect, select, text
from sqlalchemy.engine import Engine

from packages.persistence.demo import accounts
from packages.persistence.demo_read_audit import digest, read_bindings, require, snapshot
from packages.persistence.demo_read_recovery import (
    _backup_digest,
    _clock,
    _file,
    _private_directory,
    _raw,
    _stable,
    _sync_directory,
    _valid,
    _write_new,
    verify_recovery_backups,
)
from packages.persistence.demo_shared_checkpoint import (
    AnchoredSharedWeightJournal,
    CheckpointContext,
    SharedWeightAnchorStore,
    checkpoints,
)
from packages.persistence.demo_shared_weight import POLICY, STARTUP_HOLD
from services.execution.demo_read_audit_bootstrap import _validate_schema, validate_account_row
from services.execution.demo_runtime_storage import RuntimeClocks

PREFIX = "unused-read-clock-"


def verify_backups(directory: Path, cp: CheckpointContext, installation: UUID) -> None:
    folders = list(directory.glob(PREFIX + "*"))
    require(len(folders) <= 4, "UNUSED_CLOCK_BACKUP_LIMIT")
    for folder in folders:
        _private_directory(folder)
        require(
            {p.name for p in folder.iterdir()} == {"original.sqlite3", "intent.json"},
            "UNUSED_CLOCK_INCOMPLETE_BACKUP",
        )
        record = json.loads(_file(folder / "intent.json", 4096))
        require(record.get("installation") == str(installation), "UNUSED_CLOCK_BACKUP_INVALID")
        require(
            set(record)
            == {
                "version",
                "scope",
                "journal",
                "installation",
                "before_revision",
                "before_digest",
                "after_digest",
                "original_sha256",
            },
            "UNUSED_CLOCK_BACKUP_INVALID",
        )
        revision = record["before_revision"]
        require(
            type(revision) is int
            and revision >= 0
            and type(record["version"]) is int
            and record["version"] == 1,
            "UNUSED_CLOCK_BACKUP_INVALID",
        )
        expected = str(
            uuid5(cp.journal, "unused-read-clock:" + str(revision) + ":" + record["before_digest"])
        )
        require(
            folder.name == PREFIX + expected
            and record["scope"] == cp.scope
            and record["journal"] == str(cp.journal),
            "UNUSED_CLOCK_BACKUP_INVALID",
        )
        for offset, key in [(0, "before_digest"), (1, "after_digest")]:
            value = cp.connection.execute(
                select(checkpoints.c.digest).where(
                    checkpoints.c.scope == cp.scope, checkpoints.c.revision == revision + offset
                )
            ).scalar_one_or_none()
            require(value == record[key], "UNUSED_CLOCK_INCOMPLETE_CHECKPOINT")
        original = _file(folder / "original.sqlite3")
        require(
            hashlib.sha256(original).hexdigest() == record["original_sha256"]
            and _backup_digest(folder / "original.sqlite3") == record["before_digest"],
            "UNUSED_CLOCK_BACKUP_INVALID",
        )


def recover_unused_clock(
    engine: Engine,
    account: str,
    directory: Path,
    scope: str,
    journal: UUID,
    installation: UUID,
    *,
    apply: bool = False,
    status_only: bool = False,
    clocks: RuntimeClocks | None = None,
) -> dict[str, Any]:
    require(apply is True, "UNUSED_CLOCK_EXPLICIT_APPLY_REQUIRED")
    require(type(status_only) is bool, "UNUSED_CLOCK_FLAGS_INVALID")
    require(account.startswith("DEMO-") and len(account) <= 80, "UNUSED_CLOCK_DEMO_REQUIRED")
    require(
        os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "false"
        and os.getenv("TRADING_MODE", "PAPER").upper() != "LIVE",
        "UNUSED_CLOCK_LIVE_CONTEXT_FORBIDDEN",
    )
    clock = clocks or RuntimeClocks()
    anchor = SharedWeightAnchorStore(engine)
    require(anchor.installation_id() == installation, "UNUSED_CLOCK_INSTALLATION_MISMATCH")
    changed = False
    with engine.connect() as control, control.begin():
        control.execute(text("SET LOCAL statement_timeout='2s'"))
        control.execute(text("SET LOCAL lock_timeout='2s'"))
        row = (
            control.execute(
                select(accounts).where(accounts.c.account_id == account).with_for_update(read=True)
            )
            .mappings()
            .one()
        )
        validate_account_row(row, scope, journal, installation, clock)
        control.execute(text("SELECT pg_advisory_xact_lock(717332001228)"))
        with _raw(directory, scope, journal, anchor) as (cp, local, path):
            tables = set(inspect(cp.connection).get_table_names())
            present = tables & {"demo_read_audit_bindings", "demo_read_audit_events"}
            require(len(present) in (0, 2), "UNUSED_CLOCK_PARTIAL_AUDIT_SCHEMA")
            if present:
                _validate_schema(cp.connection)
                require(
                    cp.connection.execute(
                        select(read_bindings.c.scope).where(read_bindings.c.scope == scope)
                    ).first()
                    is None,
                    "UNUSED_CLOCK_AUDITED_SCOPE_FORBIDDEN",
                )
            before = snapshot(local)
            meta = before[1][0]
            require(digest(before) == cp.digest, "SHARED_CHECKPOINT_MISMATCH")
            require(
                meta[:3] == [str(journal), scope, POLICY]
                and type(meta[7]) is int
                and meta[7] == 0
                and type(meta[8]) is int
                and meta[8] == 0
                and not before[2],
                "UNUSED_CLOCK_USED_OR_STOPPED",
            )
            verify_recovery_backups(directory, [])
            verify_backups(directory, cp, installation)
            initial = _clock(clock)
            if not _valid(meta, initial):
                require(not status_only, "UNUSED_CLOCK_CHANGED_DURING_WAIT")
                require(len(list(directory.glob(PREFIX + "*"))) < 4, "UNUSED_CLOCK_BACKUP_LIMIT")
                after = copy.deepcopy(before)
                after[1][0][3:7] = [initial[2], initial[0], initial[1], initial[0]]
                identity = str(
                    uuid5(journal, "unused-read-clock:" + str(cp.revision) + ":" + cp.digest)
                )
                folder = directory / (PREFIX + identity)
                original = _file(path)
                record = dict(
                    version=1,
                    scope=scope,
                    journal=str(journal),
                    installation=str(installation),
                    before_revision=cp.revision,
                    before_digest=cp.digest,
                    after_digest=digest(after),
                    original_sha256=hashlib.sha256(original).hexdigest(),
                )
                os.mkdir(folder, mode=0o700)
                _write_new(folder / "original.sqlite3", original)
                _write_new(
                    folder / "intent.json", (json.dumps(record, sort_keys=True) + "\n").encode()
                )
                _sync_directory(folder)
                _sync_directory(directory)
                require(
                    _backup_digest(folder / "original.sqlite3") == cp.digest,
                    "UNUSED_CLOCK_BACKUP_INVALID",
                )
                _stable(initial, _clock(clock))
                local.execute(
                    "UPDATE meta SET boot=?,start=?,wall=?,last=?",
                    (initial[2], initial[0], initial[1], initial[0]),
                )
                require(snapshot(local) == after, "UNUSED_CLOCK_STATE_MISMATCH")
                anchor.append(cp.connection, scope, journal, cp.revision + 1, digest(after))
                local.execute("COMMIT")
                changed = True
    shared = AnchoredSharedWeightJournal(
        directory,
        scope,
        journal,
        anchors=anchor,
        read_only=True,
        monotonic=clock.monotonic,
        wall_clock=clock.wall_clock,
        boot=clock.boot,
    )
    report = shared.view()
    return dict(
        unused_clock_recovery="RECOVERED_KEEP_PAUSED"
        if report["startup_ready"]
        else "WAIT_STARTUP_HOLD",
        startup_ready=report["startup_ready"],
        startup_hold_seconds=STARTUP_HOLD,
        shared_journal_changed=changed,
        resource_checkpoint_changed=changed,
        financial_ledger_changed=False,
        period_baselines_changed=False,
        audit_apply_attempted=False,
        api_replaced=False,
        exchange_http_attempts=0,
        order_writes=0,
        execution_approved=False,
        orders_enabled=False,
        live_trading_enabled=False,
    )
