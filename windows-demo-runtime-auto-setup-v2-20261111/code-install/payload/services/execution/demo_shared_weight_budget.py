"""Opt-in shared pressure around original managed-order and weight controls."""

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from pydantic import SecretStr

from integrations.binance_usdm.demo import Transport
from integrations.binance_usdm.demo_rate_limits import rate_headers
from integrations.binance_usdm.demo_tp_admission import published_limit
from integrations.binance_usdm.demo_tp_window_budget import ProofDeferred, scope
from integrations.binance_usdm.rest import RESTResponse
from packages.persistence.demo_shared_weight import (
    CEILING,
    SharedWeightJournal,
    SharedWeightStopped,
)
from services.execution.demo_managed_order_quota import ManagedOrderWindowBudget


@dataclass
class Attempt:
    owner: object
    token: str
    sent: bool = False


current: ContextVar[Attempt | None] = ContextVar("shared_weight_attempt", default=None)


class SharedManagedOrderWindowBudget(ManagedOrderWindowBudget):
    def __init__(self, sender: Transport, *args: Any, shared: SharedWeightJournal, **kwargs: Any):
        self.shared, self.raw_sender = shared, sender
        super().__init__(self._send_shared, *args, **kwargs)

    def configure(self, info: Any) -> None:
        super().configure(info)
        if published_limit(info) < 6000 or self.ceiling != CEILING:
            self._deny("SHARED_WEIGHT_POLICY_LIMIT_MISMATCH")

    def require_proof(self, cost: int) -> None:
        super().require_proof(cost)
        # Check the complete original proof cost before starting it. Individual
        # requests still reserve atomically; another writer cannot overspend.
        if self.shared.view()["pressure"] + cost + 128 > CEILING:
            self.deferrals += 1
            raise ProofDeferred

    def _observes_weight(self, method: str, path: str) -> bool:
        return True

    def _send_shared(
        self, method: str, path: str, query: SecretStr, key: SecretStr | None
    ) -> RESTResponse:
        attempt = current.get()
        if attempt is None or attempt.owner is not self or attempt.sent:
            raise SharedWeightStopped("SHARED_TRANSPORT_CONTEXT_REQUIRED")
        attempt.sent = True
        try:
            response = self.raw_sender(method, path, query, key)
        except Exception:
            self.shared.stop_unknown()
            raise
        if self._observes_weight(method, path):
            raw = rate_headers(response.headers).get("x-mbx-used-weight-1m")
            self.shared.finish(
                attempt.token,
                used_weight=int(raw) if raw is not None else None,
                status=response.status,
            )
        else:
            self.shared.finish_unobserved(attempt.token, status=response.status)
        return response

    def __call__(
        self, method: str, path: str, query: SecretStr, key: SecretStr | None
    ) -> RESTResponse:
        cost = self.costs.get((method, path))
        if cost is None:
            self._deny("UNMODELED_ROUTE")
        lane = scope.get()
        if (method, path) == ("DELETE", "/fapi/v1/listenKey") or (
            (method, path) == ("GET", "/fapi/v1/time") and (self.stopped or self.phase == "CLEANUP")
        ):
            lane = "RESCUE"
        try:
            token = self.shared.reserve(cost, lane)
        except Exception as error:
            if lane == "PROOF" and str(error) == "SHARED_WEIGHT_CAPACITY":
                self.deferrals += 1
                raise ProofDeferred from None
            self._deny("SHARED_PRESSURE_ADMISSION_BLOCKED")
        attempt = Attempt(self, token)
        context = current.set(attempt)
        try:
            return super().__call__(method, path, query, key)
        finally:
            current.reset(context)
            if not attempt.sent:
                # Proven local refusal: retain the modeled charge for a full
                # window, without fabricating a response or poisoning metadata.
                self.shared.finish(token, used_weight=None, status=None, sent=False)

    def diagnostics(self) -> dict[str, Any]:
        result = super().diagnostics()
        try:
            report = self.shared.view()
        except Exception:
            report = dict(shared_pressure_readable=False, execution_approved=False)
        result["shared_managed_pressure"] = report
        return result
