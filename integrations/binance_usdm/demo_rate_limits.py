"""Allowlisted rate response metadata; no credentials, cookies or raw payloads."""

from collections.abc import Mapping
from datetime import UTC
from email.utils import parsedate_to_datetime


def retry_delay_ms(value: str, now_ms: int) -> int | None:
    if len(value) > 128:
        return None
    if value.isascii() and value.isdecimal() and len(value) <= 12:
        return int(value) * 1000
    try:
        stamp = parsedate_to_datetime(value)
        if stamp.tzinfo is None:
            return None
        return max(0, int(stamp.astimezone(UTC).timestamp() * 1000) - now_ms + 1000)
    except (ValueError, TypeError, OverflowError):
        return None


def rate_headers(raw: Mapping[str, str] | None) -> dict[str, str]:
    safe = {}
    for name, value in (raw or {}).items():
        key = name.lower()
        if not isinstance(value, str):
            continue
        if key == "retry-after" and retry_delay_ms(value, 0) is not None:
            safe[key] = value
        elif (
            key == "x-mbx-used-weight-1m"
            and value.isascii()
            and value.isdecimal()
            and len(value) <= 12
        ):
            safe[key] = value
    return safe
