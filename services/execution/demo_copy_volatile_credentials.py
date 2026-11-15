"""Opt-in read-only tmpfs credential lease for a disposable DEMO copy.

No prompts, environment credential fallback, file writes, renewal or networking.
Guest checks establish local file continuity, not remote account identity.
"""

import fcntl
import hashlib
import hmac
import json
import os
import stat
import threading
from pathlib import Path
from typing import Any

from integrations.binance_usdm.demo import DemoCredentials
from packages.persistence.demo_read_audit import require
from packages.persistence.demo_response_evidence import EvidenceReadJournal
from scripts import demo_key_session as keys
from services.execution.demo_read_credential_context import (
    LocalReadCredentialContext,
    capture_bound_copy_account,
)
from services.execution.demo_runtime_read_transport import AccountedDemoReadTransport
from services.execution.demo_runtime_storage import RuntimeClocks

MAX_LEASE_SECONDS = 75
CREDENTIAL_ENV = (
    "BINANCE_DEMO_API_KEY",
    "BINANCE_DEMO_API_SECRET",
    "BINANCE_API_KEY",
    "BINANCE_API_SECRET",
    "BINANCE_LIVE_API_KEY",
    "BINANCE_LIVE_API_SECRET",
)


def _environment() -> None:
    AccountedDemoReadTransport._context()
    require(
        not any(os.getenv(name) for name in CREDENTIAL_ENV),
        "PRIVATE_READ_ENV_CREDENTIALS_FORBIDDEN",
    )


def _readonly_tmpfs() -> bool:
    found = []
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        left, sep, right = line.partition(" - ")
        fields = left.split()
        if sep and len(fields) >= 6 and fields[4] == str(keys.DIRECTORY):
            found.append(
                right.split()[0] == "tmpfs"
                and {"ro", "noexec", "nosuid", "nodev"} <= set(fields[5].split(","))
            )
    return found == [True]


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        require(key not in result, "PRIVATE_READ_DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _identity(info: os.stat_result) -> tuple[Any, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_uid,
        stat.S_IMODE(info.st_mode),
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
    )


class PrivateReadCredentialLease:
    def __init__(
        self,
        account: str,
        scope: str,
        *,
        enable_private_read: bool = False,
        copy_only: bool = False,
        clocks: RuntimeClocks | None = None,
    ):
        require(enable_private_read is True and copy_only is True, "PRIVATE_READ_DISABLED")
        _environment()
        require(_readonly_tmpfs(), "PRIVATE_READ_READONLY_TMPFS_REQUIRED")
        self._directory_fd = self._file_fd = None
        self._closed = False
        self._used = False
        self._lock = threading.RLock()
        self._account, self._scope, self._pid = account, scope, os.getpid()
        self._scope_binding = (account, scope)
        self._clocks = clocks or RuntimeClocks()
        self._clock_binding = (self._clocks.monotonic, self._clocks.wall_clock, self._clocks.boot)
        self._created_mono = self._last_mono = self._clocks.monotonic()
        self._created_wall = self._clocks.wall_clock().timestamp()
        self._boot = self._clocks.boot()
        self._salt = os.urandom(32)
        self._credentials: DemoCredentials | None = None
        try:
            self._directory_fd = keys._directory()
            try:
                fcntl.flock(self._directory_fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                require(False, "PRIVATE_READ_REGISTRATION_BUSY")
            require(
                set(os.listdir(self._directory_fd)) == {keys.FILENAME},
                "PRIVATE_READ_SINGLE_SESSION_FILE_REQUIRED",
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
                "PRIVATE_READ_FILE_INVALID",
            )
            self._file_identity = _identity(info)
            body = self._read()
            self._fingerprint = hmac.digest(self._salt, body, hashlib.sha256)
            try:
                payload = json.loads(body.decode("utf-8"), object_pairs_hook=_object)
            except (ValueError, UnicodeError):
                require(False, "PRIVATE_READ_JSON_INVALID")
                raise AssertionError("unreachable") from None
            require(
                type(payload) is dict
                and set(payload) == {"version", "created", "expires", "key", "secret"}
                and type(payload["version"]) is int
                and payload["version"] == 1
                and type(payload["created"]) is int
                and type(payload["expires"]) is int
                and payload["expires"] == payload["created"] + keys.LIFETIME_SECONDS,
                "PRIVATE_READ_SESSION_FORMAT_INVALID",
            )
            self._session_created, self._expires = payload["created"], payload["expires"]
            self._credentials = keys._values(payload["key"], payload["secret"])
            self._pair_guard = hmac.digest(
                self._salt,
                (
                    self._credentials.api_key.get_secret_value()
                    + "\0"
                    + self._credentials.secret.get_secret_value()
                ).encode(),
                hashlib.sha256,
            )
            # Validate DEMO labels and RAM context before permitting any use.
            probe = LocalReadCredentialContext(
                account,
                scope,
                self._credentials,
                enable_local_context=True,
                copy_only=True,
                clocks=self._clocks,
            )
            probe.close()
            self.verify()
        except BaseException:
            self.close()
            raise

    def __enter__(self) -> "PrivateReadCredentialLease":
        self.verify()
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def __repr__(self) -> str:
        return "PrivateReadCredentialLease(<private read-only tmpfs capability>)"

    def __reduce_ex__(self, protocol: Any) -> Any:
        require(False, "PRIVATE_READ_NOT_SERIALIZABLE")

    def __copy__(self) -> Any:
        require(False, "PRIVATE_READ_NOT_COPYABLE")

    def __deepcopy__(self, memo: Any) -> Any:
        require(False, "PRIVATE_READ_NOT_COPYABLE")

    def _read(self) -> bytes:
        assert self._file_fd is not None
        require(
            _identity(os.fstat(self._file_fd)) == self._file_identity, "PRIVATE_READ_FILE_CHANGED"
        )
        os.lseek(self._file_fd, 0, os.SEEK_SET)
        body = os.read(self._file_fd, keys.MAX_BYTES + 1)
        require(
            len(body) == self._file_identity[5]
            and _identity(os.fstat(self._file_fd)) == self._file_identity,
            "PRIVATE_READ_FILE_CHANGED",
        )
        return body

    def verify(self) -> None:
        with self._lock:
            _environment()
            require(not self._closed and self._pid == os.getpid(), "PRIVATE_READ_CLOSED_OR_FORKED")
            require(
                type(self._credentials) is DemoCredentials
                and (self._account, self._scope) == self._scope_binding,
                "PRIVATE_READ_RAM_CONTEXT_CHANGED",
            )
            assert self._credentials is not None
            require(
                hmac.compare_digest(
                    self._pair_guard,
                    hmac.digest(
                        self._salt,
                        (
                            self._credentials.api_key.get_secret_value()
                            + "\0"
                            + self._credentials.secret.get_secret_value()
                        ).encode(),
                        hashlib.sha256,
                    ),
                ),
                "PRIVATE_READ_RAM_CREDENTIALS_CHANGED",
            )
            require(
                _readonly_tmpfs()
                and self._clock_binding
                == (self._clocks.monotonic, self._clocks.wall_clock, self._clocks.boot),
                "PRIVATE_READ_MOUNT_OR_CLOCK_CHANGED",
            )
            assert self._directory_fd is not None and self._file_fd is not None
            now = self._clocks.monotonic()
            wall = self._clocks.wall_clock().timestamp()
            require(
                type(now) in (int, float)
                and self._last_mono <= now
                and 0 <= now - self._created_mono < MAX_LEASE_SECONDS
                and self._session_created <= wall < self._expires
                and self._clocks.boot() == self._boot
                and abs((wall - self._created_wall) - (now - self._created_mono)) <= 1,
                "PRIVATE_READ_EXPIRED_OR_CLOCK_CHANGED",
            )
            self._last_mono = now
            require(
                _identity(os.stat(keys.DIRECTORY, follow_symlinks=False))[:5]
                == self._directory_identity
                and _identity(os.fstat(self._directory_fd))[:5] == self._directory_identity
                and set(os.listdir(self._directory_fd)) == {keys.FILENAME}
                and _identity(
                    os.stat(keys.FILENAME, dir_fd=self._directory_fd, follow_symlinks=False)
                )
                == self._file_identity,
                "PRIVATE_READ_PATH_CHANGED",
            )
            require(
                hmac.compare_digest(
                    hmac.digest(self._salt, self._read(), hashlib.sha256), self._fingerprint
                ),
                "PRIVATE_READ_CONTENT_CHANGED",
            )

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._credentials = None
            if self._file_fd is not None:
                os.close(self._file_fd)
                self._file_fd = None
            if self._directory_fd is not None:
                fcntl.flock(self._directory_fd, fcntl.LOCK_UN)
                os.close(self._directory_fd)
                self._directory_fd = None


def capture_with_private_copy_credentials(
    settings: Any,
    shared: EvidenceReadJournal,
    directory: Path,
    lease: PrivateReadCredentialLease,
    *,
    enable_capture: bool = False,
    copy_only: bool = False,
    sender: Any,
) -> dict[str, Any]:
    require(enable_capture is True and copy_only is True, "PRIVATE_READ_CAPTURE_DISABLED")
    require(
        type(lease) is PrivateReadCredentialLease
        and isinstance(shared, EvidenceReadJournal)
        and callable(sender),
        "PRIVATE_READ_CAPTURE_WIRING_INVALID",
    )
    try:
        with lease._lock:
            lease.verify()
            require(not lease._used, "PRIVATE_READ_ONE_SHOT_ONLY")
            lease._used = True
            require(
                (shared.account, shared.scope) == (lease._account, lease._scope),
                "PRIVATE_READ_SCOPE_MISMATCH",
            )
            assert lease._credentials is not None
            context = LocalReadCredentialContext(
                lease._account,
                lease._scope,
                lease._credentials,
                enable_local_context=True,
                copy_only=True,
                clocks=lease._clocks,
            )

            def guarded(*args: Any) -> Any:
                lease.verify()
                response = sender(*args)
                lease.verify()
                return response

            result = capture_bound_copy_account(
                settings,
                shared,
                directory,
                context,
                enable_capture=True,
                copy_only=True,
                sender=guarded,
            )
            lease.verify()
            return dict(
                private_credential_read="READONLY_TMPFS_FILE_AND_LOCAL_ARCHIVE_MATCHED",
                same_file_continuity_checked=True,
                environment_fallback_used=False,
                credential_file_written=False,
                read_context=result,
                remote_account_identity_verified=False,
                credential_source_independently_verified=False,
                installation_ready=False,
                execution_approved=False,
                orders_enabled=False,
                live_trading_enabled=False,
            )
    finally:
        lease.close()
