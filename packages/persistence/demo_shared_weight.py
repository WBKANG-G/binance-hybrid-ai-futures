"""Shared cooperating-writer weight window; not a reservation of an actual IP.

Pending sends never expire. Completed charges age from completion, not intent.
Only expired completed window rows are pruned; this is resource metadata, not
financial/audit history. Unknown transport outcomes require review, never refund.
"""

import fcntl
import hashlib
import math
import os
import sqlite3
import stat
import time
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from packages.persistence.demo_order_quota import LocalQuotaStopped, canonical

WINDOW = 60.001
STARTUP_HOLD = 70.001
CEILING = 5700
RESERVES = {"ORDINARY": 640, "WATCH": 512, "PROOF": 128, "RESCUE": 0}
POLICY = hashlib.sha256(
    canonical(["SHARED_MANAGED_WEIGHT_V1", WINDOW, CEILING, RESERVES]).encode()
).hexdigest()


class SharedWeightStopped(LocalQuotaStopped):
    pass


def boot_id() -> str:
    return str(UUID(Path("/proc/sys/kernel/random/boot_id").read_text().strip()))


def require(ok: bool, code: str) -> None:
    if not ok:
        raise SharedWeightStopped(code)


class SharedWeightJournal:
    def __init__(
        self,
        directory: Path,
        scope: str,
        identity: UUID,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        boot: Callable[[], str] = boot_id,
    ):
        self.directory, self.scope, self.identity = directory, scope, identity
        self.monotonic, self.wall_clock, self.boot = monotonic, wall_clock, boot
        self._paths()
        self.db_inode = self._identity(self.path)
        self.lock_inode = self._identity(self.lock_path)
        with self._transaction():
            pass

    def _paths(self) -> None:
        require(self.scope.startswith("DEMO-") and len(self.scope) <= 80, "SHARED_SCOPE_INVALID")
        s = self.directory.lstat()
        require(
            stat.S_ISDIR(s.st_mode)
            and s.st_uid == os.getuid()
            and stat.S_IMODE(s.st_mode) == 0o700,
            "SHARED_PRIVATE_DIRECTORY_REQUIRED",
        )
        tail = hashlib.sha256(self.scope.encode()).hexdigest()
        self.path = self.directory / (tail + ".weight.sqlite3")
        self.lock_path = self.directory / (tail + ".weight.lock")

    @staticmethod
    def _identity(path: Path) -> tuple[int, int]:
        s = path.lstat()
        require(
            stat.S_ISREG(s.st_mode)
            and s.st_nlink == 1
            and s.st_uid == os.getuid()
            and stat.S_IMODE(s.st_mode) == 0o600,
            "SHARED_FILE_IDENTITY_INVALID",
        )
        return s.st_dev, s.st_ino

    @classmethod
    def create(
        cls, directory: Path, scope: str, identity: UUID, **kwargs: Any
    ) -> "SharedWeightJournal":
        obj = object.__new__(cls)
        obj.directory, obj.scope = directory, scope
        obj._paths()
        lock_fd = os.open(
            obj.lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600
        )
        try:
            cls._identity(obj.lock_path)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise SharedWeightStopped("SHARED_WRITER_BUSY") from None
            fd = os.open(
                obj.path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600
            )
            os.close(fd)
            mono = kwargs.get("monotonic", time.monotonic)()
            wall = kwargs.get("wall_clock", lambda: datetime.now(UTC))()
            boot = kwargs.get("boot", boot_id)()
            require(
                math.isfinite(mono)
                and wall.tzinfo is not None
                and wall.utcoffset() == timedelta(0),
                "SHARED_CLOCK_INVALID",
            )
            with closing(sqlite3.connect(obj.path)) as c, c:
                c.execute("PRAGMA synchronous=FULL")
                c.execute("PRAGMA journal_mode=DELETE")
                c.execute(
                    "CREATE TABLE meta(identity TEXT, scope TEXT, policy TEXT, boot TEXT, start REAL, wall REAL, last REAL, total INTEGER, stopped INTEGER)"
                )
                c.execute(
                    "INSERT INTO meta VALUES(?,?,?,?,?,?,?,0,0)",
                    (str(identity), scope, POLICY, boot, mono, wall.timestamp(), mono),
                )
                c.execute(
                    "CREATE TABLE attempts(id TEXT PRIMARY KEY, weight INTEGER NOT NULL, total_at INTEGER NOT NULL, earlier_pending INTEGER NOT NULL, done REAL, floor INTEGER, floor_total INTEGER)"
                )
            fd = os.open(obj.path, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            os.close(lock_fd)
        return cls(directory, scope, identity, **kwargs)

    @contextmanager
    def _transaction(self) -> Iterator[tuple[sqlite3.Connection, dict[str, Any], float]]:
        require(
            self._identity(self.path) == self.db_inode
            and self._identity(self.lock_path) == self.lock_inode,
            "SHARED_FILE_REPLACED",
        )
        require(self.path.stat().st_size <= 16 * 1024 * 1024, "SHARED_STORAGE_SIZE_LIMIT")
        fd = os.open(self.lock_path, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
        c = None
        try:
            require(
                (os.fstat(fd).st_dev, os.fstat(fd).st_ino) == self.lock_inode,
                "SHARED_FILE_REPLACED",
            )
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise SharedWeightStopped("SHARED_WRITER_BUSY") from None
            c = sqlite3.connect(
                self.path.resolve().as_uri() + "?mode=rw", uri=True, isolation_level=None, timeout=2
            )
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA synchronous=FULL")
            c.execute("BEGIN IMMEDIATE")
            rows = c.execute("SELECT * FROM meta").fetchall()
            require(len(rows) == 1, "SHARED_STORAGE_INVALID")
            meta = dict(rows[0])
            require(
                (meta["identity"], meta["scope"], meta["policy"])
                == (str(self.identity), self.scope, POLICY),
                "SHARED_STORAGE_BINDING_MISMATCH",
            )
            require(
                type(meta["total"]) is int
                and meta["total"] >= 0
                and type(meta["stopped"]) is int
                and meta["stopped"] in {0, 1}
                and all(
                    type(meta[k]) in {float, int} and math.isfinite(meta[k])
                    for k in ("start", "wall", "last")
                )
                and meta["last"] >= meta["start"],
                "SHARED_STORAGE_INVALID",
            )
            require(not meta["stopped"], "SHARED_PRESSURE_STOPPED")
            now, wall = self.monotonic(), self.wall_clock()
            valid = (
                self.boot() == meta["boot"]
                and math.isfinite(now)
                and now >= meta["last"]
                and isinstance(wall, datetime)
                and wall.tzinfo is not None
                and wall.utcoffset() == timedelta(0)
                and abs((wall.timestamp() - meta["wall"]) - (now - meta["start"])) <= 1
            )
            if not valid:
                c.execute("UPDATE meta SET stopped=1")
                c.execute("COMMIT")
                raise SharedWeightStopped("SHARED_CLOCK_OR_BOOT_CHANGED")
            c.execute("UPDATE meta SET last=?", (now,))
            invalid = c.execute(
                "SELECT COUNT(*) FROM attempts WHERE typeof(weight)!='integer' OR weight<1 OR weight>5700 "
                "OR typeof(total_at)!='integer' OR total_at<weight OR total_at>? "
                "OR typeof(earlier_pending)!='integer' OR earlier_pending<0 "
                "OR (done IS NULL AND (floor IS NOT NULL OR floor_total IS NOT NULL)) "
                "OR (done IS NOT NULL AND (typeof(done) NOT IN ('integer','real') OR done<? OR done>? "
                "OR typeof(floor_total)!='integer' OR floor_total<total_at OR floor_total>? "
                "OR (floor IS NOT NULL AND (typeof(floor)!='integer' OR floor<0))))",
                (meta["total"], meta["start"], now, meta["total"]),
            ).fetchone()[0]
            require(not invalid, "SHARED_STORAGE_INVALID")
            # Never prune pending sends, even after process death or hours.
            c.execute("DELETE FROM attempts WHERE done IS NOT NULL AND done <= ?", (now - WINDOW,))
            yield c, meta, now
            c.execute("COMMIT")
        except Exception:
            if c is not None and c.in_transaction:
                c.execute("ROLLBACK")
            raise
        finally:
            if c is not None:
                c.close()
            os.close(fd)

    @staticmethod
    def _pressure(c: sqlite3.Connection, total: int) -> int:
        own = int(c.execute("SELECT COALESCE(SUM(weight),0) FROM attempts").fetchone()[0])
        observed = int(
            c.execute(
                "SELECT COALESCE(MAX(floor + ? - floor_total),0) FROM attempts WHERE floor IS NOT NULL",
                (total,),
            ).fetchone()[0]
        )
        return max(own, observed)

    def reserve(self, weight: int, lane: str) -> str:
        require(
            type(weight) is int and 0 < weight <= CEILING and lane in RESERVES,
            "SHARED_REQUEST_SCOPE_INVALID",
        )
        with self._transaction() as (c, meta, now):
            require(now - meta["start"] >= STARTUP_HOLD, "SHARED_STARTUP_HOLD")
            require(
                self._pressure(c, meta["total"]) + weight + RESERVES[lane] <= CEILING,
                "SHARED_WEIGHT_CAPACITY",
            )
            pending = int(
                c.execute(
                    "SELECT COALESCE(SUM(weight),0) FROM attempts WHERE done IS NULL"
                ).fetchone()[0]
            )
            token = str(uuid4())
            total = meta["total"] + weight
            c.execute(
                "INSERT INTO attempts(id,weight,total_at,earlier_pending) VALUES(?,?,?,?)",
                (token, weight, total, pending),
            )
            c.execute("UPDATE meta SET total=?", (total,))
            return token

    def finish(
        self, token: str, *, used_weight: int | None, status: int | None, sent: bool = True
    ) -> None:
        with self._transaction() as (c, meta, now):
            row = c.execute("SELECT * FROM attempts WHERE id=?", (token,)).fetchone()
            require(row is not None and row["done"] is None, "SHARED_ATTEMPT_NOT_PENDING")
            if sent and (
                type(used_weight) is not int
                or used_weight < 0
                or type(status) is not int
                or not 100 <= status <= 599
                or status in {418, 429}
            ):
                c.execute("UPDATE meta SET stopped=1")
                # Keep pending and commit poison for all cooperating processes.
                return
            floor = (
                None
                if not sent
                else used_weight + meta["total"] - row["total_at"] + row["earlier_pending"]
            )
            c.execute(
                "UPDATE attempts SET done=?,floor=?,floor_total=? WHERE id=?",
                (now, floor, meta["total"], token),
            )

    def stop_unknown(self) -> None:
        with self._transaction() as (c, meta, now):
            c.execute("UPDATE meta SET stopped=1")

    def view(self) -> dict[str, Any]:
        with self._transaction() as (c, meta, now):
            pending = c.execute(
                "SELECT COUNT(*), COALESCE(SUM(weight),0) FROM attempts WHERE done IS NULL"
            ).fetchone()
            return dict(
                scope="COOPERATING_WRITERS_WITH_SHARED_STORE_ONLY",
                pressure=self._pressure(c, meta["total"]),
                pending_attempts=pending[0],
                pending_weight=pending[1],
                ceiling=CEILING,
                startup_ready=now - meta["start"] >= STARTUP_HOLD,
                actual_ip_capacity_reserved=False,
                execution_approved=False,
            )
