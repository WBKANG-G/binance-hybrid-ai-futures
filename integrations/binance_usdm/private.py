"""Offline private-stream lease/order-event lifecycle; no credential acquisition or network calls.

A confirmed listenKey lasts 60 minutes per the official contract. Session validity
never asserts that account state is reconciled or that trading is authorized.
"""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import SecretStr

from integrations.binance_usdm.streams import PrivateOrderEvent, normalize_private, stream_url
from services.market_data.normalization import utc_ms


@dataclass
class PrivateStreamLease:
    listen_key: SecretStr = field(repr=False)
    confirmed_at: datetime
    expires_at: datetime = field(init=False)
    invalidated: bool = field(default=False, init=False)
    _last_by_type: dict[str, datetime] = field(default_factory=dict, init=False, repr=False)
    _seen: dict[str, None] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.confirmed_at.tzinfo is None:
            raise ValueError("Confirmed session time must have a timezone")
        stream_url([], listen_key=self.listen_key)
        self.confirmed_at = self.confirmed_at.astimezone(UTC)
        self.expires_at = self.confirmed_at + timedelta(minutes=60)

    def require_active(self, now: datetime) -> None:
        if now.tzinfo is None or self.invalidated or not self.confirmed_at <= now < self.expires_at:
            self.invalidated = True
            raise ValueError("Private session is untrusted; obtain a new confirmed session")

    def url(self, now: datetime) -> SecretStr:
        self.require_active(now)
        result = stream_url([], listen_key=self.listen_key)
        if not isinstance(result, SecretStr):
            raise ValueError("Private URL must be masked")
        return result

    def confirm_renewal(self, confirmed_at: datetime) -> None:
        # Call only after a separately authorized keepalive has actually succeeded.
        self.require_active(confirmed_at)
        self.confirmed_at = confirmed_at.astimezone(UTC)
        self.expires_at = self.confirmed_at + timedelta(minutes=60)

    def accept(self, raw: dict[str, Any], *, now: datetime) -> PrivateOrderEvent | None:
        self.require_active(now)
        try:
            kind = raw["e"]
            if kind == "listenKeyExpired":
                self.invalidated = True
                return None
            stamp = utc_ms(raw["E"])
            previous = self._last_by_type.get(kind)
            if stamp > now or (previous is not None and stamp < previous):
                raise ValueError("Private event order/clock requires reconciliation")
            digest = hashlib.sha256(
                json.dumps(raw, sort_keys=True, default=str).encode()
            ).hexdigest()
            if digest in self._seen:
                return None
            if kind in {"ORDER_TRADE_UPDATE", "ALGO_UPDATE"}:
                result = normalize_private(raw)
            elif kind in {"ACCOUNT_UPDATE", "ACCOUNT_CONFIG_UPDATE", "MARGIN_CALL", "TRADE_LITE"}:
                result = (
                    None  # Account persistence/reconciliation belongs to later approved phases.
                )
            else:
                raise ValueError("Unknown private event type")
            self._last_by_type[kind] = stamp
            self._seen[digest] = None
            if len(self._seen) > 2000:
                self._seen.pop(next(iter(self._seen)))
            return result
        except (KeyError, TypeError, ValueError):
            self.invalidated = True
            raise ValueError(
                "Invalid private event; new session and reconciliation required"
            ) from None
