"""Explicit NAVER self-email configuration, private RAM only, absolute 12h lifetime."""

import argparse
import getpass
import json
import os
import re
import stat
import time
import warnings
from dataclasses import dataclass

from pydantic import SecretStr

from scripts import demo_key_session as keys

FILENAME = "email.json"


@dataclass(frozen=True)
class EmailSettings:
    address: str
    password: SecretStr

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}@naver\.com", self.address):
            raise ValueError("NAVER_SELF_ADDRESS_REQUIRED")
        value = self.password.get_secret_value()
        if not 1 <= len(value) <= 512 or not all(33 <= ord(c) <= 126 for c in value):
            raise ValueError("SMTP_APP_PASSWORD_INVALID")


def _load_checked() -> tuple[EmailSettings, int] | None:
    keys._context()
    if not keys.DIRECTORY.exists():
        return None
    directory = keys._directory()
    try:
        try:
            fd = os.open(FILENAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        except FileNotFoundError:
            return None
        with os.fdopen(fd, "rb") as file:
            info = os.fstat(file.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_nlink != 1
                or not 1 <= info.st_size <= 4096
            ):
                raise ValueError("EMAIL_SESSION_FILE_INVALID")
            payload = json.loads(file.read(4097))
        if (
            not isinstance(payload, dict)
            or set(payload) != {"version", "created", "expires", "address", "password"}
            or type(payload["version"]) is not int
            or payload["version"] != 1
            or type(payload["created"]) is not int
            or type(payload["expires"]) is not int
            or payload["expires"] != payload["created"] + keys.LIFETIME_SECONDS
        ):
            raise ValueError("EMAIL_SESSION_FILE_INVALID")
        if not payload["created"] <= int(time.time()) < payload["expires"]:
            raise ValueError("EMAIL_SESSION_EXPIRED")
        if not isinstance(payload["address"], str) or not isinstance(payload["password"], str):
            raise ValueError("EMAIL_SESSION_FILE_INVALID")
        return EmailSettings(payload["address"], SecretStr(payload["password"])), payload["expires"]
    finally:
        os.close(directory)


def load() -> EmailSettings | None:
    loaded = _load_checked()
    return loaded[0] if loaded is not None else None


def public_status() -> dict[str, str | int | None]:
    """Return only validity/expiry metadata; never expose address/password/settings."""
    try:
        loaded = _load_checked()
        if loaded is None:
            return {"state": "NOT_CONFIGURED", "expires_unix": None, "remaining_seconds": None}
        remaining = loaded[1] - int(time.time())
        if remaining <= 0:
            raise ValueError("EMAIL_SESSION_EXPIRED")
        return {
            "state": "CONFIGURED_PRIVATE_RAM",
            "expires_unix": loaded[1],
            "remaining_seconds": remaining,
        }
    except Exception:  # noqa: BLE001 -- no private settings or raw failure in public metadata
        return {
            "state": "BLOCKED_EXPIRED_OR_INVALID",
            "expires_unix": None,
            "remaining_seconds": None,
        }


def configure() -> None:
    keys._context()
    if load() is not None:
        return
    with keys._registration_lock():
        if load() is not None:
            return
        warnings.simplefilter("error", getpass.GetPassWarning)
        value = EmailSettings(
            input("NAVER self email address: ").strip(),
            SecretStr(getpass.getpass("NAVER app password (hidden): ")),
        )
        now = int(time.time())
        data = json.dumps(
            {
                "version": 1,
                "created": now,
                "expires": now + keys.LIFETIME_SECONDS,
                "address": value.address,
                "password": value.password.get_secret_value(),
            }
        ).encode()
        directory = keys._directory()
        try:
            fd = os.open(
                FILENAME,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory,
            )
            try:
                with os.fdopen(fd, "wb") as file:
                    os.fchmod(file.fileno(), 0o600)
                    file.write(data)
                    file.flush()
            except BaseException:
                os.unlink(FILENAME, dir_fd=directory)
                raise
        finally:
            os.close(directory)


def clear() -> None:
    keys._context()
    if not keys.DIRECTORY.exists():
        return
    with keys._registration_lock():
        directory = keys._directory()
        try:
            try:
                os.unlink(FILENAME, dir_fd=directory)
            except FileNotFoundError:
                pass
        finally:
            os.close(directory)


def status() -> str:
    try:
        return "CONFIGURED_PRIVATE_RAM" if load() is not None else "NOT_CONFIGURED"
    except Exception:
        return "BLOCKED_EXPIRED_OR_INVALID"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--enable-naver-email", action="store_true")
    actions.add_argument("--status", action="store_true")
    actions.add_argument("--clear", action="store_true")
    args = parser.parse_args()
    try:
        if args.enable_naver_email:
            configure()
        elif args.clear:
            clear()
        print(
            json.dumps(
                {
                    "email_session": status(),
                    "maximum_lifetime_hours": 12,
                    "persistent_password_storage": False,
                    "recipient_scope": "NAVER_AUTHENTICATED_SELF_ONLY",
                    "orders_enabled": False,
                    "live_trading_enabled": False,
                }
            ),
            flush=True,
        )
    except Exception:
        print(
            json.dumps(
                {
                    "email_session": "BLOCKED",
                    "failure": "EMAIL_CONFIGURATION_FAILED_NO_SECRET_OUTPUT",
                    "orders_enabled": False,
                    "live_trading_enabled": False,
                }
            ),
            flush=True,
        )
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
