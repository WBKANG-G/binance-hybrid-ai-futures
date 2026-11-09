"""Durable managed-writer quota journal; never an account-wide usage counter.

The caller binds the immutable journal ID and flow records in the fenced DEMO
ledger. Opening never creates/reinitializes a missing file. A process lock lasts
across HTTP; a separate FULL-synchronous commit consumes a role before any send.
"""

import fcntl
import hashlib
import json
import math
import os
import sqlite3
import stat
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4, uuid5

from integrations.binance_usdm.demo import DemoFailure

RESTART_HOLD_SECONDS = 70.001
FLOW_SLOTS = 6
MAX_EVENTS = 10_000
POLICY_VERSION = "MANAGED_WRITERS_ONLY_V1"
ROLES = frozenset({"ENTRY", "SL", "TP", "EXIT"})


class LocalQuotaStopped(DemoFailure):
    pass


def quota_require(ok: bool, code: str) -> None:
    if not ok:
        raise LocalQuotaStopped(code)


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def utc_stamp(value: datetime) -> str:
    quota_require(
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
        and value.utcoffset() == timedelta(0),
        "QUOTA_UTC_CLOCK_INVALID",
    )
    return value.isoformat()


@dataclass(frozen=True)
class LocalQuotaPolicy:
    slots_10s: int = 6
    slots_60s: int = 12

    def __post_init__(self) -> None:
        if (
            any(
                type(n) is not int or not FLOW_SLOTS <= n <= 1200
                for n in (self.slots_10s, self.slots_60s)
            )
            or self.slots_10s > self.slots_60s
        ):
            raise ValueError("Invalid managed-writer quota policy")

    @property
    def digest(self) -> str:
        return hashlib.sha256(
            canonical(
                {
                    "version": POLICY_VERSION,
                    "slots10": self.slots_10s,
                    "slots60": self.slots_60s,
                    "flow_slots": FLOW_SLOTS,
                }
            ).encode()
        ).hexdigest()


DEFAULT_LOCAL_POLICY = LocalQuotaPolicy()


class DurableOrderQuota:
    def __init__(
        self,
        directory: Path,
        account: str,
        journal_id: UUID,
        policy: LocalQuotaPolicy = DEFAULT_LOCAL_POLICY,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self.directory = directory
        self.account, self.journal_id, self.policy = account, journal_id, policy
        self.monotonic, self.wall_clock = monotonic, wall_clock
        self.pid = os.getpid()
        self.lock = threading.RLock()
        self.fd = -1
        self.connection: sqlite3.Connection | None = None
        self.session_id: str | None = None
        self.session_mon = self.last_mon = monotonic()
        self.session_wall = wall_clock()
        self.clock_fault = False
        self.owner: str | None = None
        self.fence: int | None = None
        self._paths()
        try:
            self._acquire()
            quota_require(self.path.is_file(), "QUOTA_JOURNAL_MISSING")
            self.db_inode = self._file_identity(self.path)
            self.connection = sqlite3.connect(
                self.path.resolve().as_uri() + "?mode=rw",
                uri=True,
                timeout=2,
                isolation_level=None,
                check_same_thread=False,
            )
            self.connection.execute("PRAGMA synchronous=FULL")
            quota_require(
                self.connection.execute("PRAGMA integrity_check").fetchone() == ("ok",),
                "QUOTA_JOURNAL_CORRUPT",
            )
            row = self.connection.execute(
                "SELECT identity, account, policy FROM identity"
            ).fetchall()
            quota_require(
                row == [(str(journal_id), account, policy.digest)], "QUOTA_STORAGE_BINDING_MISMATCH"
            )
            self._events()
        except Exception:
            self.close()
            raise

    def _paths(self) -> None:
        quota_require(
            self.account.startswith("DEMO-") and len(self.account) <= 80, "QUOTA_ACCOUNT_INVALID"
        )
        quota_require(
            self.directory.is_dir() and not self.directory.is_symlink(),
            "QUOTA_PRIVATE_DIRECTORY_REQUIRED",
        )
        s = self.directory.stat()
        quota_require(
            s.st_uid == os.getuid() and stat.S_IMODE(s.st_mode) == 0o700,
            "QUOTA_PRIVATE_DIRECTORY_REQUIRED",
        )
        name = hashlib.sha256(self.account.encode()).hexdigest()
        self.path = self.directory / (name + ".sqlite3")
        self.lock_path = self.directory / (name + ".lock")

    @staticmethod
    def _file_identity(path: Path) -> tuple[int, int]:
        s = path.lstat()
        quota_require(
            stat.S_ISREG(s.st_mode)
            and s.st_uid == os.getuid()
            and s.st_nlink == 1
            and stat.S_IMODE(s.st_mode) == 0o600,
            "QUOTA_FILE_IDENTITY_INVALID",
        )
        return s.st_dev, s.st_ino

    def _acquire(self) -> None:
        self.fd = os.open(
            self.lock_path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600
        )
        self.lock_inode = self._file_identity(self.lock_path)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise LocalQuotaStopped("QUOTA_MANAGED_WRITER_BUSY") from None

    @classmethod
    def create(
        cls,
        directory: Path,
        account: str,
        journal_id: UUID,
        policy: LocalQuotaPolicy = DEFAULT_LOCAL_POLICY,
        **kwargs: Any,
    ) -> "DurableOrderQuota":
        # Explicit bootstrap only. The coordinator writes the ID to PostgreSQL
        # after this synced file exists; it must refuse a second bootstrap.
        obj = object.__new__(cls)
        obj.directory, obj.account = directory, account
        obj._paths()
        fd = os.open(obj.lock_path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        try:
            cls._file_identity(obj.lock_path)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise LocalQuotaStopped("QUOTA_MANAGED_WRITER_BUSY") from None
            created = os.open(
                obj.path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600
            )
            os.close(created)
            connection = sqlite3.connect(obj.path, isolation_level=None)
            try:
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute("PRAGMA journal_mode=DELETE")
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "CREATE TABLE identity (identity TEXT NOT NULL, account TEXT NOT NULL, policy TEXT NOT NULL)"
                )
                connection.execute(
                    "CREATE TABLE events (seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, payload TEXT NOT NULL, prior_hash TEXT NOT NULL, hash TEXT NOT NULL)"
                )
                connection.execute(
                    "CREATE TRIGGER quota_no_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT,'append_only'); END"
                )
                connection.execute(
                    "CREATE TRIGGER quota_no_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT,'append_only'); END"
                )
                connection.execute(
                    "CREATE TRIGGER quota_identity_no_update BEFORE UPDATE ON identity BEGIN SELECT RAISE(ABORT,'immutable_identity'); END"
                )
                connection.execute(
                    "CREATE TRIGGER quota_identity_no_delete BEFORE DELETE ON identity BEGIN SELECT RAISE(ABORT,'immutable_identity'); END"
                )
                connection.execute(
                    "INSERT INTO identity VALUES (?,?,?)", (str(journal_id), account, policy.digest)
                )
                connection.execute("COMMIT")
            finally:
                connection.close()
            file_fd = os.open(obj.path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
            try:
                os.fsync(file_fd)
            finally:
                os.close(file_fd)
            dir_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        finally:
            os.close(fd)
        return cls(directory, account, journal_id, policy, **kwargs)

    def owns_process_lock(self) -> bool:
        try:
            return (
                os.getpid() == self.pid
                and self.fd >= 0
                and self._file_identity(self.lock_path) == self.lock_inode
            )
        except (OSError, LocalQuotaStopped):
            return False

    def _identity(self) -> sqlite3.Connection:
        quota_require(
            os.getpid() == self.pid and self.fd >= 0 and self.connection is not None,
            "QUOTA_PROCESS_OWNERSHIP_INVALID",
        )
        quota_require(
            self._file_identity(self.lock_path) == self.lock_inode
            and self._file_identity(self.path) == self.db_inode,
            "QUOTA_FILE_REPLACED",
        )
        quota_require(self.path.stat().st_size <= 16 * 1024 * 1024, "QUOTA_JOURNAL_SIZE_LIMIT")
        assert self.connection is not None
        return self.connection

    def _events(self) -> list[dict[str, Any]]:
        c = self._identity()
        rows = c.execute(
            "SELECT seq,event_id,kind,payload,prior_hash,hash FROM events ORDER BY seq"
        ).fetchall()
        quota_require(len(rows) <= MAX_EVENTS, "QUOTA_JOURNAL_SIZE_LIMIT")
        result = []
        prior = "0" * 64
        for index, (seq, event_id, kind, payload, old, digest) in enumerate(rows, 1):
            expected = hashlib.sha256(
                canonical([index, event_id, kind, payload, prior]).encode()
            ).hexdigest()
            quota_require(
                seq == index and old == prior and digest == expected, "QUOTA_JOURNAL_CHAIN_INVALID"
            )
            data = json.loads(payload)
            quota_require(
                isinstance(data, dict)
                and data.get("journal") == str(self.journal_id)
                and data.get("policy") == self.policy.digest,
                "QUOTA_JOURNAL_PAYLOAD_INVALID",
            )
            result.append({"seq": seq, "id": event_id, "kind": kind, "data": data, "hash": digest})
            prior = digest
        high = c.execute("SELECT seq FROM sqlite_sequence WHERE name='events'").fetchone()
        quota_require(
            (high is None and not rows) or high == (len(rows),), "QUOTA_JOURNAL_CHAIN_INVALID"
        )
        return result

    def _append(self, kind: str, key: str, data: dict[str, Any]) -> dict[str, Any]:
        c = self._identity()
        c.execute("BEGIN IMMEDIATE")
        try:
            records = self._events()
            quota_require(len(records) < MAX_EVENTS, "QUOTA_JOURNAL_SIZE_LIMIT")
            event_id = str(uuid5(self.journal_id, key))
            quota_require(all(r["id"] != event_id for r in records), "QUOTA_EVENT_ALREADY_CONSUMED")
            data = dict(data, journal=str(self.journal_id), policy=self.policy.digest)
            payload = canonical(data)
            seq = len(records) + 1
            prior = records[-1]["hash"] if records else "0" * 64
            digest = hashlib.sha256(
                canonical([seq, event_id, kind, payload, prior]).encode()
            ).hexdigest()
            c.execute(
                "INSERT INTO events(event_id,kind,payload,prior_hash,hash) VALUES (?,?,?,?,?)",
                (event_id, kind, payload, prior, digest),
            )
            c.execute("COMMIT")
            return {"seq": seq, "id": event_id, "kind": kind, "data": data, "hash": digest}
        except Exception:
            if c.in_transaction:
                c.execute("ROLLBACK")
            raise

    def start_session(self, owner: UUID, fence: int) -> None:
        quota_require(os.getpid() == self.pid, "QUOTA_PROCESS_OWNERSHIP_INVALID")
        with self.lock:
            quota_require(
                self.session_id is None and type(fence) is int and fence > 0,
                "QUOTA_SESSION_INVALID",
            )
            self.session_mon = self.last_mon = self.monotonic()
            self.session_wall = self.wall_clock()
            utc_stamp(self.session_wall)
            quota_require(math.isfinite(self.session_mon), "QUOTA_MONOTONIC_INVALID")
            self.session_id = str(uuid4())
            self.owner = str(owner)
            self.fence = fence
            self._append(
                "SESSION",
                self.session_id,
                {
                    "session": self.session_id,
                    "owner": self.owner,
                    "fence": fence,
                    "at": utc_stamp(self.session_wall),
                },
            )

    def _elapsed(self) -> float:
        now = self.monotonic()
        wall = self.wall_clock()
        if not math.isfinite(now) or now < self.last_mon or not isinstance(wall, datetime):
            self.clock_fault = True
        else:
            try:
                utc_stamp(wall)
                if abs((wall - self.session_wall).total_seconds() - (now - self.session_mon)) > 1:
                    self.clock_fault = True
            except (LocalQuotaStopped, TypeError):
                self.clock_fault = True
        if math.isfinite(now):
            self.last_mon = max(self.last_mon, now)
        return now - self.session_mon if not self.clock_fault else -1

    def _flows(self) -> dict[str, dict[str, Any]]:
        flows: dict[str, dict[str, Any]] = {}
        for r in self._events():
            d = r["data"]
            kind = r["kind"]
            if kind == "SESSION":
                continue
            flow = d.get("flow")
            quota_require(isinstance(flow, str), "QUOTA_FLOW_INVALID")
            if kind == "RESERVE":
                quota_require(
                    flow not in flows
                    and set(d.get("roles", {})) == ROLES
                    and d.get("slots") == FLOW_SLOTS,
                    "QUOTA_FLOW_INVALID",
                )
                flows[flow] = {"reserve": r, "claims": {}, "seal": None}
            else:
                quota_require(flow in flows and flows[flow]["seal"] is None, "QUOTA_FLOW_INVALID")
                if kind == "CLAIM":
                    role = d.get("role")
                    quota_require(
                        role in ROLES and role not in flows[flow]["claims"], "QUOTA_FLOW_INVALID"
                    )
                    flows[flow]["claims"][role] = r
                elif kind == "SEAL":
                    flows[flow]["seal"] = r
                else:
                    raise LocalQuotaStopped("QUOTA_EVENT_KIND_INVALID")
        return flows

    def view(self) -> dict[str, Any]:
        quota_require(os.getpid() == self.pid, "QUOTA_PROCESS_OWNERSHIP_INVALID")
        with self.lock:
            elapsed = self._elapsed()
            flows = self._flows()
            ready = self.session_id is not None and elapsed >= RESTART_HOLD_SECONDS
            pressure = {10: 0, 60: 0}
            active = []
            for flow, record in flows.items():
                seal = record["seal"]
                if seal is None:
                    active.append(flow)
                    for period in pressure:
                        pressure[period] += FLOW_SLOTS
                    continue
                for period in pressure:
                    if not ready or (
                        seal["data"]["session"] == self.session_id
                        and elapsed - seal["data"]["elapsed"] < period + 0.001
                    ):
                        pressure[period] += FLOW_SLOTS
            return {
                "scope": "MANAGED_WRITERS_WITH_SHARED_JOURNAL_ONLY",
                "external_account_usage": "UNKNOWN",
                "cold_start_hold_seconds": RESTART_HOLD_SECONDS,
                "hold_elapsed_seconds": max(0, elapsed),
                "local_window_ready": ready,
                "clock_trusted": not self.clock_fault,
                "slots_10s": pressure[10] if ready else None,
                "retained_model_slots_10s": pressure[10],
                "slots_60s": pressure[60] if ready else None,
                "retained_model_slots_60s": pressure[60],
                "cap_10s": self.policy.slots_10s,
                "cap_60s": self.policy.slots_60s,
                "active_flows": active,
                "reserved_slots_per_flow": FLOW_SLOTS,
                "execution_approved": False,
            }

    def reserve(self, flow: UUID, roles: dict[str, str]) -> dict[str, Any]:
        quota_require(os.getpid() == self.pid, "QUOTA_PROCESS_OWNERSHIP_INVALID")
        with self.lock:
            v = self.view()
            records = self._events()
            quota_require(
                v["local_window_ready"] and v["clock_trusted"], "QUOTA_RESTART_HOLD_ACTIVE"
            )
            quota_require(not v["active_flows"], "QUOTA_PRIOR_FLOW_UNRESOLVED")
            quota_require(
                v["slots_10s"] + FLOW_SLOTS <= self.policy.slots_10s
                and v["slots_60s"] + FLOW_SLOTS <= self.policy.slots_60s,
                "QUOTA_LOCAL_CAPACITY_INSUFFICIENT",
            )
            quota_require(len(records) + 8 <= MAX_EVENTS, "QUOTA_JOURNAL_SIZE_LIMIT")
            quota_require(
                set(roles) == ROLES
                and len(set(roles.values())) == 4
                and all(isinstance(i, str) and 1 <= len(i) <= 36 for i in roles.values()),
                "QUOTA_ROLE_SCOPE_INVALID",
            )
            return self._append(
                "RESERVE",
                "reserve:" + str(flow),
                {
                    "flow": str(flow),
                    "roles": roles,
                    "slots": FLOW_SLOTS,
                    "session": self.session_id,
                    "owner": self.owner,
                    "fence": self.fence,
                    "at": utc_stamp(self.wall_clock()),
                },
            )

    def claim(self, flow: UUID, role: str, identifier: str) -> dict[str, Any]:
        quota_require(os.getpid() == self.pid, "QUOTA_PROCESS_OWNERSHIP_INVALID")
        with self.lock:
            quota_require(self.session_id is not None, "QUOTA_SESSION_INVALID")
            f = self._flows().get(str(flow))
            quota_require(f is not None and f["seal"] is None, "QUOTA_FLOW_NOT_ACTIVE")
            assert f is not None
            d = f["reserve"]["data"]
            quota_require(
                role in ROLES and d["roles"].get(role) == identifier, "QUOTA_ROLE_SCOPE_INVALID"
            )
            quota_require(role not in f["claims"], "QUOTA_EVENT_ALREADY_CONSUMED")
            quota_require(
                d["session"] == self.session_id or role == "EXIT", "QUOTA_RECOVERY_EXIT_ONLY"
            )
            return self._append(
                "CLAIM",
                "claim:" + str(flow) + ":" + role,
                {
                    "flow": str(flow),
                    "role": role,
                    "identifier": identifier,
                    "session": self.session_id,
                },
            )

    def seal(self, flow: UUID) -> dict[str, Any]:
        # Only the coordinator's original complete flat proof may call this.
        quota_require(os.getpid() == self.pid, "QUOTA_PROCESS_OWNERSHIP_INVALID")
        with self.lock:
            quota_require(self.session_id is not None, "QUOTA_SESSION_INVALID")
            elapsed = self._elapsed()
            quota_require(elapsed >= 0 and not self.clock_fault, "QUOTA_CLOCK_UNTRUSTED")
            f = self._flows().get(str(flow))
            quota_require(f is not None and f["seal"] is None, "QUOTA_FLOW_NOT_ACTIVE")
            return self._append(
                "SEAL",
                "seal:" + str(flow),
                {
                    "flow": str(flow),
                    "slots": FLOW_SLOTS,
                    "session": self.session_id,
                    "elapsed": elapsed,
                    "at": utc_stamp(self.wall_clock()),
                },
            )

    def flow_records(self) -> dict[str, dict[str, Any]]:
        quota_require(os.getpid() == self.pid, "QUOTA_PROCESS_OWNERSHIP_INVALID")
        with self.lock:
            return self._flows()

    def close(self) -> None:
        if os.getpid() != self.pid:
            if self.connection is not None:
                self.connection.close()
                self.connection = None
            if self.fd >= 0:
                os.close(self.fd)
                self.fd = -1
            return
        with self.lock:
            if getattr(self, "connection", None) is not None:
                assert self.connection is not None
                self.connection.close()
                self.connection = None
            if getattr(self, "fd", -1) >= 0:
                # A forked child must not unlock its parent's open description.
                if os.getpid() == self.pid:
                    fcntl.flock(self.fd, fcntl.LOCK_UN)
                os.close(self.fd)
                self.fd = -1
