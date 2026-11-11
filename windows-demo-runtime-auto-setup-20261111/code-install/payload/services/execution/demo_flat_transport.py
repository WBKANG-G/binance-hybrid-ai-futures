"""Bounded confirmation transport; original flat/cleanup proof remains mandatory."""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from pydantic import SecretStr

from integrations.binance_usdm.demo import DemoClient, DemoFailure, transport
from integrations.binance_usdm.recovery_transport import GET_PATHS, RecoveryTransport
from integrations.binance_usdm.rest import RESTResponse
from services.execution.demo_recovery_budget import CLOCK_REFRESH_MS, RecoveryBudget


class ConfirmationClient(DemoClient):
    def __init__(self, source: DemoClient, pooled: RecoveryTransport | None):
        super().__init__(
            source.settings, source.credentials, send=source.send, clock_ms=source.clock_ms
        )
        self.source = source
        self.pooled = pooled
        self.budget = RecoveryBudget()
        self.last_route = "NONE"
        self.cleanup_attempts = 0

    def sync_clock(self) -> None:
        self.budget.probe()
        try:
            self.source.sync_clock()
        finally:
            self.budget.check()

    def request(self, method: str, path: str, params: Mapping[str, str] | None = None) -> Any:
        if method == "GET" and path in GET_PATHS:
            self.last_route = "SIGNED_GET"
        elif (
            method == "DELETE"
            and path == "/fapi/v1/algoOrder"
            and self.settings.demo_trading_enabled
        ):
            if not params or set(params) != {"clientAlgoId"} or not params["clientAlgoId"]:
                raise DemoFailure("Flat confirmation cleanup scope forbidden")
            self.last_route = "PROTECTION_DELETE"
            self.cleanup_attempts += 1
        else:
            raise DemoFailure("Flat confirmation forbids entry/exit POST and other mutations")
        self.budget.signed()
        synced = self.source.synced_at
        if synced is None or not 0 <= self.source.clock_ms() - synced < CLOCK_REFRESH_MS:
            self.sync_clock()
        try:
            return self.source.request(method, path, params)
        finally:
            self.budget.check()

    def diagnostics(self) -> dict[str, str | int]:
        return {
            **(
                self.pooled.diagnostics()
                if self.pooled
                else {"audit_transport_mode": "UNPOOLED_TRANSPORT"}
            ),
            "confirmation_signed_requests": self.budget.signed_queries,
            "confirmation_clock_probes": self.budget.clock_probes,
            "confirmation_cleanup_attempts": self.cleanup_attempts,
            "confirmation_last_route": self.last_route,
        }


@contextmanager
def confirmation_context(source: DemoClient) -> Iterator[ConfirmationClient]:
    original = source.send
    pooled = RecoveryTransport() if original is transport else None

    def dispatch(
        method: str, path: str, query: SecretStr, api_key: SecretStr | None
    ) -> RESTResponse:
        if method == "GET" and path in GET_PATHS and pooled is not None:
            return pooled(method, path, query, api_key)
        if method == "DELETE" and path == "/fapi/v1/algoOrder":
            return original(method, path, query, api_key)
        raise DemoFailure("Flat confirmation transport route forbidden")

    try:
        if pooled is not None:
            source.send = dispatch
        yield ConfirmationClient(source, pooled)
    finally:
        source.send = original
        if pooled is not None:
            pooled.close()
