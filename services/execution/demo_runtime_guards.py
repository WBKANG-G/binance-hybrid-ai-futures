"""Startup snapshots and per-request client wiring for the synthetic runtime."""

import os
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal

from pydantic import SecretStr

from integrations.binance_usdm.demo import DemoSettings
from packages.persistence.demo_order_quota import quota_require
from services.execution.demo_models import DemoLedger
from services.execution.demo_tp_uncertain_cancel_candidate import UncertainCancellationClient
from services.risk.paper import period_keys


def require_start_context() -> None:
    quota_require(
        os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "false"
        and os.getenv("TRADING_MODE", "PAPER").upper() != "LIVE",
        "RUNTIME_START_CONTEXT_CHANGED",
    )


def require_start_ledger(
    ledger: DemoLedger, settings: DemoSettings, history: int, now: datetime
) -> None:
    quota_require(
        ledger.policy == settings.policy
        and ledger.margin_mode == settings.margin_mode
        and ledger.symbols == settings.symbols,
        "RUNTIME_START_LEDGER_SCOPE_CHANGED",
    )
    quota_require(
        ledger.paused and ledger.risk_state.value == "NORMAL", "RUNTIME_START_RISK_CHANGED"
    )
    quota_require(
        not any(ledger.positions.values())
        and all(
            not o.remaining and not o.open_quantity and o.position_state.value == "CLOSED"
            for o in ledger.orders.values()
        ),
        "RUNTIME_START_EXPOSURE_CHANGED",
    )
    quota_require(len(ledger.orders) == history, "RUNTIME_START_HISTORY_CHANGED")
    quota_require(
        period_keys(now, ledger.policy.week_start_kst) == (ledger.daily_key, ledger.weekly_key),
        "RUNTIME_PERIOD_KEYS_MISMATCH",
    )


class RuntimeBoundClient(UncertainCancellationClient):
    """A mutated sender/credential/scope cannot bypass the owned budget later."""

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._runtime_sender = self.send
        self._runtime_credentials = self.credentials
        self._runtime_settings = self.settings.model_copy(update={"demo_trading_enabled": False})

    def _require_binding(self) -> None:
        quota_require(
            self.send is self._runtime_sender and self.credentials is self._runtime_credentials,
            "RUNTIME_CLIENT_WIRING_CHANGED",
        )
        quota_require(
            isinstance(self.settings, DemoSettings)
            and self.settings.model_copy(update={"demo_trading_enabled": False})
            == self._runtime_settings,
            "RUNTIME_CLIENT_SETTINGS_CHANGED",
        )

    def public(self, path: str, params: Mapping[str, str] | None = None) -> Any:
        self._require_binding()
        return super().public(path, params)

    def request(self, method: str, path: str, params: Mapping[str, str] | None = None) -> Any:
        self._require_binding()
        return super().request(method, path, params)

    def listen_key(self, method: Literal["POST", "PUT", "DELETE"]) -> SecretStr | None:
        self._require_binding()
        return super().listen_key(method)
