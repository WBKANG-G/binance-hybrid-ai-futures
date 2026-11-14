"""Explicit clock rebase for audited, fully completed DEMO GET activity only.

No pending outcome, stopped store, or checkpoint gap is repaired. Prior rows,
cumulative charges, audit events and checkpoint revisions are preserved.
"""

import copy
import hashlib
import math
import os
import re
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

from packages.persistence import demo_read_audit as audit_module
from packages.persistence.demo_read_audit import (
    AuditedDemoReadJournal,
    ReadAuditStore,
    digest,
    require,
    snapshot,
)
from packages.persistence.demo_shared_checkpoint import CheckpointContext, SharedWeightAnchorStore
from packages.persistence.demo_shared_weight import POLICY, STARTUP_HOLD, SharedWeightJournal
from services.execution.demo_runtime_storage import RuntimeClocks

PREFIX = "read-clock-recovery-"
MAX_BACKUPS = 8
MAX_BACKUP_BYTES = 129 * 1024 * 1024
MAX_DB_BYTES = 16 * 1024 * 1024
RECOVERY_POLICY = "AUDITED_COMPLETED_READ_CLOCK_REBASE_V1"


def _clock(clocks: RuntimeClocks) -> tuple[float, float, str]:
    mono, wall, boot = clocks.monotonic(), clocks.wall_clock(), clocks.boot()
    require(
        type(mono) in (int, float)
        and math.isfinite(mono)
        and mono >= 0
        and isinstance(wall, datetime)
        and wall.tzinfo is not None
        and wall.utcoffset() == UTC.utcoffset(wall)
        and isinstance(boot, str)
        and re.fullmatch("[A-Za-z0-9-]{1,128}", boot) is not None,
        "READ_RECOVERY_CURRENT_CLOCK_INVALID",
    )
    return float(mono), wall.timestamp(), boot


def _valid(meta: list[Any], clock: tuple[float, float, str]) -> bool:
    mono, wall, boot = clock
    return bool(
        boot == meta[3] and mono >= meta[6] and abs((wall - meta[5]) - (mono - meta[4])) <= 1
    )


def _stable(first: tuple[float, float, str], last: tuple[float, float, str]) -> None:
    require(
        first[2] == last[2]
        and last[0] >= first[0]
        and abs((last[1] - first[1]) - (last[0] - first[0])) <= 1,
        "READ_RECOVERY_CLOCK_CHANGED_DURING_APPLY",
    )


def _operation(journal: str, revision: int, before_digest: str) -> str:
    return str(uuid5(UUID(journal), "completed-read-clock:" + str(revision) + ":" + before_digest))


def validate_rebase_transition(before: Any, after: Any, action: dict[str, Any]) -> None:
    old, new = audit_module._rows(before), audit_module._rows(after)
    b, a = before[1][0], after[1][0]
    require(
        set(action)
        == {"kind", "path", "cost", "token", "status", "weight", "sent", "observe", "recovery"}
        and action["kind"] == "CLOCK_REBASE"
        and action["sent"] is False
        and action["observe"] is True
        and all(action[k] is None for k in ("path", "cost", "token", "status", "weight")),
        "READ_RECOVERY_ACTION_INVALID",
    )
    data = action["recovery"]
    require(
        isinstance(data, dict)
        and set(data)
        == {"version", "operation_id", "before_revision", "before_digest", "backup_sha256"}
        and type(data["version"]) is int
        and data["version"] == 1
        and type(data["before_revision"]) is int
        and data["before_revision"] >= 0
        and data["before_digest"] == digest(before)
        and data["operation_id"] == _operation(b[0], data["before_revision"], data["before_digest"])
        and isinstance(data["backup_sha256"], str)
        and re.fullmatch("[0-9a-f]{64}", data["backup_sha256"]) is not None,
        "READ_RECOVERY_ACTION_INVALID",
    )
    require(
        before[0] == after[0]
        and b[:3] == a[:3]
        and a[2] == POLICY
        and type(b[7]) is int
        and type(a[7]) is int
        and type(b[8]) is int
        and type(a[8]) is int
        and b[7] >= 0
        and a[7:] == b[7:] == [b[7], 0]
        and all(type(x) in (float, int) and math.isfinite(x) for x in a[4:7])
        and a[4] == a[6]
        and a[4] >= 0
        and isinstance(a[3], str)
        and re.fullmatch("[A-Za-z0-9-]{1,128}", a[3]) is not None
        and not _valid(b, (a[4], a[5], a[3])),
        "READ_RECOVERY_TRANSITION_INVALID",
    )
    require(
        old.keys() == new.keys() and all(row[4] is not None for row in old.values()),
        "READ_RECOVERY_PENDING_OR_ROW_CHANGE",
    )
    require(
        all(new[k] == row[:4] + [a[4], row[5], row[6]] for k, row in old.items()),
        "READ_RECOVERY_CHARGES_CHANGED",
    )


def _private_directory(path: Path) -> None:
    info = path.lstat()
    require(
        stat.S_ISDIR(info.st_mode)
        and info.st_uid == os.getuid()
        and stat.S_IMODE(info.st_mode) == 0o700,
        "READ_RECOVERY_PRIVATE_DIRECTORY_REQUIRED",
    )


def _file(path: Path, limit: int = MAX_DB_BYTES) -> bytes:
    expected = SharedWeightJournal._identity(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        require(
            (info.st_dev, info.st_ino) == expected and info.st_size <= limit,
            "READ_RECOVERY_BACKUP_INVALID",
        )
        with os.fdopen(fd, "rb", closefd=False) as stream:
            body = stream.read(limit + 1)
        final = os.fstat(fd)
        require(
            len(body) <= limit
            and SharedWeightJournal._identity(path) == expected
            and (info.st_size, info.st_mtime_ns) == (final.st_size, final.st_mtime_ns),
            "READ_RECOVERY_BACKUP_INVALID",
        )
        return body
    finally:
        os.close(fd)


def _intent(data: dict[str, Any], after_digest: str) -> bytes:
    from packages.persistence.demo_order_quota import canonical

    return (
        canonical(dict(policy=RECOVERY_POLICY, recovery=data, after_digest=after_digest)) + "\n"
    ).encode()


def _backup_digest(path: Path) -> str:
    c = sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        return digest(snapshot(c))
    finally:
        c.close()


def recovery_layout_witness(directory: Path, rows: list[dict[str, Any]]) -> str:
    expected = {
        PREFIX + row["action"]["recovery"]["operation_id"]: row
        for row in rows
        if row["action"]["kind"] == "CLOCK_REBASE"
    }
    found = {}
    for path in directory.iterdir():
        if path.name.startswith(PREFIX):
            found[path.name] = path
            require(len(found) <= MAX_BACKUPS, "READ_RECOVERY_BACKUP_LIMIT")
    require(found.keys() == expected.keys(), "READ_RECOVERY_UNFINISHED_OR_MISSING_BACKUP")
    values = []
    for name, folder in sorted(found.items()):
        _private_directory(folder)
        require(
            {p.name for p in folder.iterdir()} == {"original.sqlite3", "intent.json"},
            "READ_RECOVERY_BACKUP_INVALID",
        )
        info = folder.lstat()
        stats: list[tuple[int, ...]] = [
            (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns)
        ]
        for leaf in ("original.sqlite3", "intent.json"):
            path = folder / leaf
            SharedWeightJournal._identity(path)
            info = path.lstat()
            stats.append(
                (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            )
        values.append([name, expected[name]["action"], expected[name]["checkpoint_digest"], stats])
    return digest(values)


def verify_recovery_backups(directory: Path, rows: list[dict[str, Any]]) -> None:
    expected = {
        PREFIX + row["action"]["recovery"]["operation_id"]: row
        for row in rows
        if row["action"]["kind"] == "CLOCK_REBASE"
    }
    require(len(expected) <= MAX_BACKUPS, "READ_RECOVERY_BACKUP_LIMIT")
    found = {}
    for path in directory.iterdir():
        if path.name.startswith(PREFIX):
            found[path.name] = path
            require(len(found) <= MAX_BACKUPS, "READ_RECOVERY_BACKUP_LIMIT")
    require(found.keys() == expected.keys(), "READ_RECOVERY_UNFINISHED_OR_MISSING_BACKUP")
    total = 0
    for name, row in expected.items():
        folder = found[name]
        _private_directory(folder)
        require(
            {p.name for p in folder.iterdir()} == {"original.sqlite3", "intent.json"},
            "READ_RECOVERY_BACKUP_INVALID",
        )
        data = row["action"]["recovery"]
        backup = folder / "original.sqlite3"
        body = _file(backup)
        intent = _file(folder / "intent.json", 4096)
        total += len(body) + len(intent)
        require(
            hashlib.sha256(body).hexdigest() == data["backup_sha256"]
            and _backup_digest(backup) == data["before_digest"]
            and intent == _intent(data, row["checkpoint_digest"]),
            "READ_RECOVERY_BACKUP_INVALID",
        )
    require(total <= MAX_BACKUP_BYTES, "READ_RECOVERY_BACKUP_LIMIT")


def _write_new(path: Path, body: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(body)
            stream.flush()
            os.fsync(fd)
    finally:
        os.close(fd)


def _sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def _raw(
    directory: Path, scope: str, journal: UUID, anchor: SharedWeightAnchorStore
) -> Iterator[tuple[CheckpointContext, sqlite3.Connection, Path]]:
    import fcntl

    obj = object.__new__(SharedWeightJournal)
    obj.directory = directory
    obj.scope = scope
    obj._paths()
    db_id, lock_id = obj._identity(obj.path), obj._identity(obj.lock_path)
    require(obj.path.stat().st_size <= MAX_DB_BYTES, "READ_RECOVERY_BACKUP_LIMIT")
    with anchor.scope(scope, journal, read_only=False) as cp:
        assert isinstance(cp, CheckpointContext)
        fd = os.open(obj.lock_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        c = None
        try:
            require((os.fstat(fd).st_dev, os.fstat(fd).st_ino) == lock_id, "SHARED_FILE_REPLACED")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                require(False, "SHARED_WRITER_BUSY")
            require(
                obj._identity(obj.path) == db_id and obj._identity(obj.lock_path) == lock_id,
                "SHARED_FILE_REPLACED",
            )
            require(
                not any(
                    Path(str(obj.path) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")
                ),
                "READ_RECOVERY_SQLITE_SIDECAR_PRESENT",
            )
            c = sqlite3.connect(
                obj.path.resolve().as_uri() + "?mode=rw", uri=True, isolation_level=None, timeout=2
            )
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA synchronous=FULL")
            c.execute("BEGIN IMMEDIATE")
            yield cp, c, obj.path
            if c.in_transaction:
                c.execute("COMMIT")
        except Exception:
            if c is not None and c.in_transaction:
                c.execute("ROLLBACK")
            raise
        finally:
            if c is not None:
                c.close()
            os.close(fd)


def recover_completed_read_clock(
    directory: Path,
    scope: str,
    journal: UUID,
    *,
    anchor: SharedWeightAnchorStore,
    audit: ReadAuditStore,
    apply: bool = False,
    clocks: RuntimeClocks | None = None,
) -> dict[str, Any]:
    require(apply is True, "READ_RECOVERY_EXPLICIT_APPLY_REQUIRED")
    require(anchor.engine is audit.engine, "READ_AUDIT_ANCHOR_ENGINE_REQUIRED")
    clocks = clocks or RuntimeClocks()
    changed = False
    with _raw(directory, scope, journal, anchor) as (cp, c, path):
        # V1 CLOCK_REBASE has no matching V2 response-evidence transition.
        # Deny before creating intents or changing SQLite/PG, including orphans.
        from sqlalchemy import inspect, text

        v2 = {"read_response_evidence_bindings_v2", "read_response_evidence_events_v2"}
        present = set(inspect(cp.connection).get_table_names()) & v2
        require(len(present) in (0, 2), "READ_RECOVERY_PARTIAL_V2_SCHEMA")
        witness = directory / (
            hashlib.sha256(scope.encode()).hexdigest() + ".response-evidence-v2.log"
        )
        bound = (
            bool(present)
            and cp.connection.execute(
                text("SELECT 1 FROM read_response_evidence_bindings_v2 WHERE scope=:scope"),
                dict(scope=scope),
            ).first()
            is not None
        )
        require(
            not bound and not witness.exists() and not witness.is_symlink(),
            "READ_RECOVERY_V2_EVIDENCE_REQUIRES_REVIEW",
        )
        rows, before = audit._verified_history(cp, c)
        verify_recovery_backups(directory, rows)
        meta = before[1][0]
        require(
            meta[:3] == [str(journal), scope, POLICY] and meta[8] == 0,
            "READ_RECOVERY_STOPPED_OR_BINDING_INVALID",
        )
        require(all(row[4] is not None for row in before[2]), "READ_RECOVERY_PENDING_REQUESTS")
        initial = _clock(clocks)
        if not _valid(meta, initial):
            require(len(rows) + 3 <= audit_module.MAX_RECORDS, "READ_RECOVERY_AUDIT_CAPACITY")
            backups = [row for row in rows if row["action"]["kind"] == "CLOCK_REBASE"]
            require(len(backups) < MAX_BACKUPS, "READ_RECOVERY_BACKUP_LIMIT")
            identity = _operation(str(journal), cp.revision, cp.digest)
            folder = directory / (PREFIX + identity)
            require(
                not folder.exists() and not folder.is_symlink(),
                "READ_RECOVERY_UNFINISHED_OR_MISSING_BACKUP",
            )
            original = _file(path)
            used = sum(
                p.stat().st_size
                for row in backups
                for p in (
                    directory / (PREFIX + row["action"]["recovery"]["operation_id"])
                ).iterdir()
            )
            require(used + len(original) + 4096 <= MAX_BACKUP_BYTES, "READ_RECOVERY_BACKUP_LIMIT")
            os.mkdir(folder, mode=0o700)
            _write_new(folder / "original.sqlite3", original)
            _sync_directory(folder)
            _sync_directory(directory)
            require(
                _backup_digest(folder / "original.sqlite3") == cp.digest,
                "READ_RECOVERY_BACKUP_INVALID",
            )
            current = _clock(clocks)
            _stable(initial, current)
            require(not _valid(meta, current), "READ_RECOVERY_CLOCK_CHANGED_DURING_APPLY")
            after = copy.deepcopy(before)
            after[1][0][3:7] = [current[2], current[0], current[1], current[0]]
            for row in after[2]:
                row[4] = current[0]
            data = dict(
                version=1,
                operation_id=identity,
                before_revision=cp.revision,
                before_digest=cp.digest,
                backup_sha256=hashlib.sha256(original).hexdigest(),
            )
            action = dict(
                kind="CLOCK_REBASE",
                path=None,
                cost=None,
                token=None,
                status=None,
                weight=None,
                sent=False,
                observe=True,
                recovery=data,
            )
            validate_rebase_transition(before, after, action)
            _write_new(folder / "intent.json", _intent(data, digest(after)))
            _sync_directory(folder)
            _stable(current, _clock(clocks))
            c.execute(
                "UPDATE meta SET boot=?,start=?,wall=?,last=?",
                (current[2], current[0], current[1], current[0]),
            )
            c.execute("UPDATE attempts SET done=?", (current[0],))
            require(snapshot(c) == after, "READ_RECOVERY_CHARGES_CHANGED")
            audit.append(cp, c, before, action)
            anchor.append(cp.connection, scope, journal, cp.revision + 1, digest(after))
            c.execute("COMMIT")
            changed = True
    shared = AuditedDemoReadJournal(
        directory,
        scope,
        journal,
        anchors=anchor,
        audit=audit,
        read_only=True,
        monotonic=clocks.monotonic,
        wall_clock=clocks.wall_clock,
        boot=clocks.boot,
    )
    report = audit.review(shared)
    return report | dict(
        read_clock_recovery="RECOVERED_KEEP_PAUSED"
        if report["startup_ready"]
        else "WAIT_STARTUP_HOLD",
        shared_journal_changed=changed,
        resource_checkpoint_changed=changed,
        clock_recovery_record_appended=changed,
        financial_ledger_changed=False,
        period_baselines_changed=False,
        startup_hold_seconds=STARTUP_HOLD,
    )
