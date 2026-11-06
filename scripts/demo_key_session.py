"""Explicit volatile DEMO credentials, only on a private Docker tmpfs mount.

Never stores credentials in an image, bind mount, database, config or command line.
The files are plaintext within protected tmpfs; same-user/root processes can read
memory. Host memory/swap/snapshots remain subject to the host's own protection.
"""

import argparse
import fcntl
import getpass
import json
import os
import stat
import time
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from pydantic import SecretStr

from integrations.binance_usdm.demo import DemoCredentials

DIRECTORY = Path("/run/demo-keys")
FILENAME = "session.json"
LIFETIME_SECONDS = 12 * 3600
MAX_BYTES = 4096


class SessionKeyError(ValueError):
    """Safe fixed codes only; never contains file content or credential values."""


def _context() -> None:
    if (
        os.getenv("LIVE_TRADING_ENABLED", "false").lower() != "false"
        or os.getenv("TRADING_MODE", "PAPER") == "LIVE"
        or any(
            os.getenv(k)
            for k in (
                "BINANCE_API_KEY",
                "BINANCE_API_SECRET",
                "BINANCE_LIVE_API_KEY",
                "BINANCE_LIVE_API_SECRET",
            )
        )
    ):
        raise SessionKeyError("DEMO_ONLY_CONTEXT_REQUIRED")


def _tmpfs() -> bool:
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        left, sep, right = line.partition(" - ")
        fields = left.split()
        if sep and len(fields) >= 6 and fields[4] == str(DIRECTORY):
            return right.split()[0] == "tmpfs" and {"noexec", "nosuid", "nodev"} <= set(
                fields[5].split(",")
            )
    return False


def _directory() -> int:
    try:
        if not _tmpfs():
            raise SessionKeyError("PRIVATE_TMPFS_REQUIRED")
        fd = os.open(DIRECTORY, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            os.close(fd)
            raise SessionKeyError("PRIVATE_TMPFS_PERMISSIONS_REQUIRED")
        return fd
    except OSError:
        raise SessionKeyError("PRIVATE_TMPFS_UNAVAILABLE") from None


def _values(key: object, secret: object) -> DemoCredentials:
    if any(
        not isinstance(v, str) or not 1 <= len(v) <= 512 or not all(33 <= ord(c) <= 126 for c in v)
        for v in (key, secret)
    ):
        raise SessionKeyError("DEMO_CREDENTIAL_INPUT_INVALID")
    assert isinstance(key, str) and isinstance(secret, str)
    return DemoCredentials(SecretStr(key), SecretStr(secret))


def _load() -> tuple[DemoCredentials, int] | None:
    if not DIRECTORY.exists():
        return None
    directory = _directory()
    try:
        try:
            fd = os.open(FILENAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        except FileNotFoundError:
            return None
        with os.fdopen(fd, "rb") as file:
            info = os.fstat(file.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_uid != os.getuid()
                or info.st_nlink != 1
                or info.st_size > MAX_BYTES
            ):
                raise SessionKeyError("KEY_SESSION_FILE_INVALID")
            payload = json.loads(file.read(MAX_BYTES + 1))
        if (
            not isinstance(payload, dict)
            or set(payload) != {"version", "created", "expires", "key", "secret"}
            or type(payload["version"]) is not int
            or payload["version"] != 1
            or type(payload["created"]) is not int
            or type(payload["expires"]) is not int
            or payload["expires"] != payload["created"] + LIFETIME_SECONDS
        ):
            raise SessionKeyError("KEY_SESSION_FILE_INVALID")
        now = int(time.time())
        if not payload["created"] <= now < payload["expires"]:
            raise SessionKeyError("KEY_SESSION_EXPIRED_CLEAR_AND_REGISTER")
        return _values(payload["key"], payload["secret"]), payload["expires"]
    except (OSError, ValueError, UnicodeError) as error:
        if isinstance(error, SessionKeyError):
            raise
        raise SessionKeyError("KEY_SESSION_FILE_INVALID") from None
    finally:
        os.close(directory)


def credentials(*, prompt: bool = False) -> DemoCredentials:
    _context()
    # Explicit runtime credentials still take precedence. Partial/mixed values
    # must never silently fall back to another account's cached pair.
    if os.getenv("BINANCE_DEMO_API_KEY") or os.getenv("BINANCE_DEMO_API_SECRET"):
        return DemoCredentials.from_environment()
    loaded = _load()
    if loaded is not None:
        return loaded[0]
    if not prompt:
        return DemoCredentials.from_environment()
    return ensure()


def _register_locked() -> None:
    _context()
    directory = _directory()
    try:
        # Refuse replacement before prompting. Explicit clear avoids accidental
        # account changes between a preview and its apply operation.
        try:
            os.stat(FILENAME, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise SessionKeyError("KEY_SESSION_EXISTS_CLEAR_FIRST")
        warnings.simplefilter("error", getpass.GetPassWarning)
        value = _values(
            getpass.getpass("DEMO API Key (hidden): "), getpass.getpass("DEMO Secret (hidden): ")
        )
        now = int(time.time())
        data = json.dumps(
            {
                "version": 1,
                "created": now,
                "expires": now + LIFETIME_SECONDS,
                "key": value.api_key.get_secret_value(),
                "secret": value.secret.get_secret_value(),
            }
        ).encode()
        fd = os.open(
            FILENAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory
        )
        try:
            with os.fdopen(fd, "wb") as file:
                os.fchmod(file.fileno(), 0o600)
                file.write(data)
                file.flush()
        except BaseException:
            os.unlink(FILENAME, dir_fd=directory)
            raise
    except OSError:
        raise SessionKeyError("KEY_SESSION_REGISTRATION_FAILED") from None
    finally:
        os.close(directory)


def _clear_locked() -> None:
    _context()
    if not DIRECTORY.exists():
        return
    directory = _directory()
    try:
        try:
            os.unlink(FILENAME, dir_fd=directory)
        except FileNotFoundError:
            pass
    except OSError:
        raise SessionKeyError("KEY_SESSION_CLEAR_FAILED") from None
    finally:
        os.close(directory)


@contextmanager
def _registration_lock() -> Iterator[None]:
    # Lock the checked tmpfs directory inode; no lock file or secret is written.
    directory = _directory()
    try:
        try:
            fcntl.flock(directory, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SessionKeyError("KEY_SESSION_REGISTRATION_IN_PROGRESS") from None
        except OSError:
            raise SessionKeyError("KEY_SESSION_LOCK_FAILED") from None
        try:
            yield
        finally:
            fcntl.flock(directory, fcntl.LOCK_UN)
    finally:
        os.close(directory)


def register() -> None:
    _context()
    with _registration_lock():
        _register_locked()


def clear() -> None:
    _context()
    if not DIRECTORY.exists():
        return
    with _registration_lock():
        _clear_locked()


def ensure() -> DemoCredentials:
    """Reuse a valid binding; first hidden input is cached without extending expiry."""
    _context()
    if os.getenv("BINANCE_DEMO_API_KEY") or os.getenv("BINANCE_DEMO_API_SECRET"):
        try:
            return DemoCredentials.from_environment()
        except ValueError:
            raise SessionKeyError("RUNTIME_DEMO_CREDENTIALS_INVALID") from None
    loaded = _load()
    if loaded is not None:
        return loaded[0]
    with _registration_lock():
        loaded = _load()
        if loaded is None:
            _register_locked()
            loaded = _load()
        if loaded is None:
            raise SessionKeyError("KEY_SESSION_NOT_REGISTERED_AFTER_ENSURE")
        return loaded[0]


ERROR_CODES = frozenset(
    {
        "DEMO_ONLY_CONTEXT_REQUIRED",
        "PRIVATE_TMPFS_REQUIRED",
        "PRIVATE_TMPFS_PERMISSIONS_REQUIRED",
        "PRIVATE_TMPFS_UNAVAILABLE",
        "DEMO_CREDENTIAL_INPUT_INVALID",
        "KEY_SESSION_FILE_INVALID",
        "KEY_SESSION_EXPIRED_CLEAR_AND_REGISTER",
        "KEY_SESSION_EXISTS_CLEAR_FIRST",
        "KEY_SESSION_REGISTRATION_FAILED",
        "KEY_SESSION_CLEAR_FAILED",
        "KEY_SESSION_REGISTRATION_IN_PROGRESS",
        "KEY_SESSION_LOCK_FAILED",
        "KEY_SESSION_NOT_REGISTERED_AFTER_ENSURE",
        "RUNTIME_DEMO_CREDENTIALS_INVALID",
    }
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--register", action="store_true")
    actions.add_argument("--ensure", action="store_true")
    actions.add_argument("--status", action="store_true")
    actions.add_argument("--clear", action="store_true")
    args = parser.parse_args()
    try:
        _context()
        if args.ensure:
            ensure()
            state = (
                "RUNTIME_CREDENTIALS_AVAILABLE"
                if (os.getenv("BINANCE_DEMO_API_KEY") or os.getenv("BINANCE_DEMO_API_SECRET"))
                else "AVAILABLE"
            )
        elif args.register:
            register()
            state = "REGISTERED_IN_PRIVATE_TMPFS"
        elif args.clear:
            clear()
            state = "CLEARED"
        else:
            state = "AVAILABLE" if _load() is not None else "NOT_REGISTERED"
        print(
            json.dumps(
                {
                    "demo_key_session": state,
                    "maximum_lifetime_hours": 12,
                    "persistent_key_storage": False,
                    "orders_enabled": False,
                    "live_trading_enabled": False,
                }
            )
        )
    except (ValueError, OSError, EOFError, getpass.GetPassWarning) as error:
        print(
            json.dumps(
                {
                    "demo_key_session": "BLOCKED_CHECK_SESSION_OR_REGISTER",
                    "error_code": str(error)
                    if isinstance(error, SessionKeyError) and str(error) in ERROR_CODES
                    else "KEY_SESSION_OPERATION_FAILED",
                    "orders_enabled": False,
                    "live_trading_enabled": False,
                }
            )
        )
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
