"""GET-only, finite recovery audit without discarding historical requests."""

import time
from collections.abc import Callable, Mapping
from typing import Any, Literal

from integrations.binance_usdm.demo import DemoClient, DemoFailure

MAX_HISTORY_REQUESTS = 128
AUDIT_BATCH_SIZE = 8
MAX_SIGNED_QUERIES = 600
MAX_CLOCK_PROBES = 6
MAX_AUDIT_SECONDS = 40.0
CLOCK_REFRESH_MS = 20000


class AuditBudgetExceeded(DemoFailure):
    def __init__(
        self,
        code: Literal[
            "REQUEST_AUDIT_TIME_BUDGET", "REQUEST_AUDIT_QUERY_BUDGET", "REQUEST_AUDIT_CLOCK_BUDGET"
        ],
    ):
        self.code = code
        super().__init__("Recovery audit resource budget exceeded; keep lock")


class RecoveryBudget:
    def __init__(self, clock: Callable[[], float] | None = None):
        self.clock = clock or time.monotonic
        self.started = self.clock()
        self.signed_queries = 0
        self.clock_probes = 0

    def check(self) -> None:
        elapsed = self.clock() - self.started
        if not 0 <= elapsed < MAX_AUDIT_SECONDS:
            raise AuditBudgetExceeded("REQUEST_AUDIT_TIME_BUDGET")

    def signed(self) -> None:
        self.check()
        if self.signed_queries >= MAX_SIGNED_QUERIES:
            raise AuditBudgetExceeded("REQUEST_AUDIT_QUERY_BUDGET")
        self.signed_queries += 1

    def probe(self) -> None:
        self.check()
        if self.clock_probes >= MAX_CLOCK_PROBES:
            raise AuditBudgetExceeded("REQUEST_AUDIT_CLOCK_BUDGET")
        self.clock_probes += 1


class RecoveryReadClient(DemoClient):
    def __init__(self, source: DemoClient, budget: RecoveryBudget | None = None):
        if source.settings.demo_trading_enabled:
            raise DemoFailure("Recovery audit requires read-only DEMO client")
        super().__init__(
            source.settings, source.credentials, send=source.send, clock_ms=source.clock_ms
        )
        self.source = source
        self.budget = budget or RecoveryBudget()

    def sync_clock(self) -> None:
        self.budget.probe()
        try:
            self.source.sync_clock()
        finally:
            self.budget.check()

    def public(self, path: str, params: Mapping[str, str] | None = None) -> Any:
        if path != "/fapi/v1/time" or params:
            raise DemoFailure("Recovery audit public route forbidden")
        self.budget.probe()
        try:
            return self.source.public(path, params)
        finally:
            self.budget.check()

    def request(self, method: str, path: str, params: Mapping[str, str] | None = None) -> Any:
        if method != "GET":
            raise DemoFailure("Recovery audit forbids exchange mutations")
        self.budget.signed()
        now = self.source.clock_ms()
        synced = self.source.synced_at
        if synced is None or not 0 <= now - synced < CLOCK_REFRESH_MS:
            self.sync_clock()
        self.budget.check()
        try:
            # Delegate to the original SDK/client, preserving its guards and
            # injected transport/contract tests. Never retries a failed query.
            return self.source.request(method, path, params)
        finally:
            self.budget.check()
