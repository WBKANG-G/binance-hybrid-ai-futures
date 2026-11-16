"""Synthetic-file continuity during local preparation; grants no signing authority.

A single 75-second credential lease is created only at the capture boundary.
This guard holds read-only FDs/SH lock and a digest, never a credential object.
"""

import fcntl
import hashlib
import hmac
import json
import math
import os
import re
import stat
import threading
from typing import Any

from packages.persistence.demo_read_audit import require
from scripts import demo_key_session as keys
from services.execution.demo_copy_evidence_rehearsal import (
    SYNTHETIC_PRIVATE_KEY,
    SYNTHETIC_PRIVATE_SECRET,
)
from services.execution.demo_copy_volatile_credentials import (
    PrivateReadCredentialLease,
    _environment,
    _identity,
    _object,
    _readonly_tmpfs,
)
from services.execution.demo_runtime_storage import RuntimeClocks

MAX_PREPARATION_SECONDS = 300


class SyntheticPrivatePreparation:
    def __init__(
        self,
        account: str,
        scope: str,
        *,
        copy_only: bool = False,
        synthetic_only: bool = False,
        clocks: RuntimeClocks | None = None,
    ):
        require(copy_only is True and synthetic_only is True, "PRIVATE_PREPARATION_DISABLED")
        require(
            all(
                isinstance(v, str) and re.fullmatch(r"DEMO-[A-Za-z0-9_-]{1,74}", v)
                for v in (account, scope)
            ),
            "PRIVATE_PREPARATION_SCOPE_INVALID",
        )
        self._lock = threading.RLock()
        self._directory_fd = self._file_fd = None
        self._closed = self._spent = False
        self._account, self._scope, self._pid = account, scope, os.getpid()
        self._binding = (account, scope)
        self._clocks = clocks or RuntimeClocks()
        self._clock_binding = (self._clocks.monotonic, self._clocks.wall_clock, self._clocks.boot)
        self._start = self._last = self._clocks.monotonic()
        self._wall = self._clocks.wall_clock().timestamp()
        self._boot = self._clocks.boot()
        self._salt = os.urandom(32)
        self.preparation_seconds: float | None = None
        try:
            _environment()
            require(_readonly_tmpfs(), "PRIVATE_PREPARATION_READONLY_TMPFS_REQUIRED")
            self._directory_fd = keys._directory()
            try:
                fcntl.flock(self._directory_fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                require(False, "PRIVATE_PREPARATION_REGISTRATION_BUSY")
            require(
                set(os.listdir(self._directory_fd)) == {keys.FILENAME},
                "PRIVATE_PREPARATION_FILE_SET_CHANGED",
            )
            self._directory_identity = _identity(os.fstat(self._directory_fd))[:5]
            self._file_fd = os.open(
                keys.FILENAME,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=self._directory_fd,
            )
            info = os.fstat(self._file_fd)
            require(
                stat.S_ISREG(info.st_mode)
                and info.st_uid == os.getuid()
                and stat.S_IMODE(info.st_mode) == 0o600
                and info.st_nlink == 1
                and 0 < info.st_size <= keys.MAX_BYTES,
                "PRIVATE_PREPARATION_FILE_INVALID",
            )
            self._file_identity = _identity(info)
            body = self._read()
            payload = json.loads(body.decode("utf-8"), object_pairs_hook=_object)
            require(
                type(payload) is dict
                and set(payload) == {"version", "created", "expires", "key", "secret"}
                and type(payload["version"]) is int
                and payload["version"] == 1
                and type(payload["created"]) is int
                and type(payload["expires"]) is int
                and payload["expires"] == payload["created"] + keys.LIFETIME_SECONDS,
                "PRIVATE_PREPARATION_SESSION_INVALID",
            )
            require(
                type(payload["key"]) is str
                and type(payload["secret"]) is str
                and payload["key"].isascii()
                and payload["secret"].isascii()
                and hmac.compare_digest(payload["key"], SYNTHETIC_PRIVATE_KEY)
                and hmac.compare_digest(payload["secret"], SYNTHETIC_PRIVATE_SECRET),
                "PRIVATE_PREPARATION_SYNTHETIC_KEYS_REQUIRED",
            )
            self._created, self._expires = payload["created"], payload["expires"]
            self._digest = hmac.digest(self._salt, body, hashlib.sha256)
            self.verify()
        except BaseException:
            self.close()
            raise

    def __repr__(self) -> str:
        return "SyntheticPrivatePreparation(<file continuity only; no signing capability>)"

    def __reduce_ex__(self, protocol: Any) -> Any:
        require(False, "PRIVATE_PREPARATION_NOT_SERIALIZABLE")

    def __copy__(self) -> Any:
        require(False, "PRIVATE_PREPARATION_NOT_COPYABLE")

    def __deepcopy__(self, memo: Any) -> Any:
        require(False, "PRIVATE_PREPARATION_NOT_COPYABLE")

    def _read(self) -> bytes:
        assert self._file_fd is not None
        require(
            _identity(os.fstat(self._file_fd)) == self._file_identity,
            "PRIVATE_PREPARATION_FILE_CHANGED",
        )
        os.lseek(self._file_fd, 0, os.SEEK_SET)
        body = os.read(self._file_fd, keys.MAX_BYTES + 1)
        require(
            len(body) == self._file_identity[5]
            and _identity(os.fstat(self._file_fd)) == self._file_identity,
            "PRIVATE_PREPARATION_FILE_CHANGED",
        )
        return body

    def verify(self) -> None:
        with self._lock:
            _environment()
            require(
                not self._closed
                and self._pid == os.getpid()
                and self._binding == (self._account, self._scope),
                "PRIVATE_PREPARATION_CLOSED_OR_CONTEXT_CHANGED",
            )
            require(
                _readonly_tmpfs()
                and self._clock_binding
                == (self._clocks.monotonic, self._clocks.wall_clock, self._clocks.boot),
                "PRIVATE_PREPARATION_MOUNT_OR_CLOCK_CHANGED",
            )
            now, wall = self._clocks.monotonic(), self._clocks.wall_clock().timestamp()
            require(
                type(now) in (int, float) and math.isfinite(now) and math.isfinite(wall),
                "PRIVATE_PREPARATION_CLOCK_INVALID",
            )
            require(self._last <= now, "PRIVATE_PREPARATION_MONOTONIC_ROLLBACK")
            require(
                0 <= now - self._start < MAX_PREPARATION_SECONDS,
                "PRIVATE_PREPARATION_DEADLINE_EXCEEDED",
            )
            require(
                self._created <= wall < self._expires,
                "PRIVATE_PREPARATION_SESSION_EXPIRED_OR_FUTURE",
            )
            require(self._clocks.boot() == self._boot, "PRIVATE_PREPARATION_BOOT_CHANGED")
            require(
                abs((wall - self._wall) - (now - self._start)) <= 1,
                "PRIVATE_PREPARATION_CLOCK_DIVERGED",
            )
            self._last = now
            assert self._directory_fd is not None and self._file_fd is not None
            require(
                _identity(os.stat(keys.DIRECTORY, follow_symlinks=False))[:5]
                == self._directory_identity
                and _identity(os.fstat(self._directory_fd))[:5] == self._directory_identity
                and set(os.listdir(self._directory_fd)) == {keys.FILENAME}
                and _identity(
                    os.stat(keys.FILENAME, dir_fd=self._directory_fd, follow_symlinks=False)
                )
                == self._file_identity,
                "PRIVATE_PREPARATION_PATH_CHANGED",
            )
            require(
                hmac.compare_digest(
                    hmac.digest(self._salt, self._read(), hashlib.sha256), self._digest
                ),
                "PRIVATE_PREPARATION_CONTENT_CHANGED",
            )

    def acquire_once(self) -> PrivateReadCredentialLease:
        with self._lock:
            self.verify()
            require(not self._spent, "PRIVATE_PREPARATION_ALREADY_SPENT")
            self._spent = True
            lease = None
            try:
                lease = PrivateReadCredentialLease(
                    self._account,
                    self._scope,
                    enable_private_read=True,
                    copy_only=True,
                    clocks=self._clocks,
                )
                self.verify()
                require(
                    lease._file_identity == self._file_identity
                    and hmac.compare_digest(
                        hmac.digest(self._salt, lease._read(), hashlib.sha256), self._digest
                    ),
                    "PRIVATE_PREPARATION_HANDOFF_CHANGED",
                )
                self.preparation_seconds = round(self._clocks.monotonic() - self._start, 3)
                return lease
            except BaseException:
                if lease is not None:
                    lease.close()
                raise
            finally:
                self.close()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            if self._file_fd is not None:
                os.close(self._file_fd)
                self._file_fd = None
            if self._directory_fd is not None:
                fcntl.flock(self._directory_fd, fcntl.LOCK_UN)
                os.close(self._directory_fd)
                self._directory_fd = None
