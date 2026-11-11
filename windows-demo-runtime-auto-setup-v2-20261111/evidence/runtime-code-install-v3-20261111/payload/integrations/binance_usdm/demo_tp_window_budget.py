"""Pre-send rolling weight gate. Explicit synthetic execution only.

Own attempts expire after 60.001 seconds, never on a lower venue header.
Headers describe shared IP pressure, not a reservation or a proven route cost.
"""

from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs

from pydantic import SecretStr

from integrations.binance_usdm.demo_rate_limits import rate_headers
from integrations.binance_usdm.demo_tp_admission import TPAdmissionBudget, TPAdmissionStopped
from integrations.binance_usdm.rest import RESTResponse

scope: ContextVar[str] = ContextVar("tp_weight_scope", default="ORDINARY")


class ProofDeferred(Exception):
    """Local resource deferral, distinct from an exchange/transport failure."""


@contextmanager
def weight_scope(name: str) -> Iterator[None]:
    token = scope.set(name)
    try:
        yield
    finally:
        scope.reset(token)


class WindowTPBudget(TPAdmissionBudget):
    WINDOW = 60.001
    RESCUE = 512
    WATCH = 128

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.attempts: deque[tuple[float, int]] = deque()
        self.header_weight = 0
        self.header_charge = 0
        self.header_sequence = 0
        self.total_charge = 0
        self.peak = 0
        self.flat_observed = False  # Resource scheduling only; never VERIFIED_FLAT.
        self.cancel_ids: set[str] = set()
        self.exit_ids: set[str] = set()
        self.owned_entry_id: str | None = None
        self.owned_quantity = Decimal(0)
        self.owned_side: str | None = None
        self.deferrals = 0

    def _pressure(self, now: float) -> int:
        while self.attempts and now - self.attempts[0][0] >= self.WINDOW:
            self.attempts.popleft()
        own = sum(cost for _, cost in self.attempts)
        observed = (
            self.header_weight + self.total_charge - self.header_charge if self._fresh(now) else 0
        )
        return max(own, observed)

    @property
    def ceiling(self) -> int:
        return (self.limit or 6000) - self.profile.headroom

    def available(self) -> int:
        with self.lock:
            return self.ceiling - self._pressure(self._time())

    def preflight(self) -> None:
        with self.lock:
            if self.limit is None or self.phase != "BOOTSTRAP" or self.stopped:
                self._deny("PREFLIGHT_SCOPE_INVALID")
            if not self._fresh(self._time()):
                self._deny("WEIGHT_METADATA_UNAVAILABLE")
            if not self.synthetic:
                self._deny("UNPROVEN_MODEL_REAL_EXECUTION_DISABLED")
            if self.available() < self.profile.entry + self.RESCUE:
                self._deny("ENTRY_AND_RESCUE_DO_NOT_FIT")
            self.phase = "PREPARATION"

    def before_preparation(self) -> None:
        with self.lock:
            if self.phase != "PREPARATION" or self.stopped:
                self._deny("PREPARATION_SCOPE_INVALID")
            self.prechecked = True

    def admit_entry(self) -> None:
        import threading

        with self.lock:
            if (
                self.phase != "PREPARATION"
                or not self.prechecked
                or self.admitted
                or self.stopped
                or not self._fresh(self._time())
                or self.available() < self.profile.entry + self.RESCUE
            ):
                self._deny("ENTRY_AND_RESCUE_NO_HEADROOM")
            self.admitted = True
            self.entry_owner = threading.get_ident()
            self.phase = "ENTRY_PROTECTION"

    def require_proof(self, cost: int) -> None:
        with self.lock:
            if self.untrusted:
                self._deny("PROOF_METADATA_UNTRUSTED")
            if self.available() < cost + self.WATCH:
                self.deferrals += 1
                raise ProofDeferred

    def __call__(
        self, method: str, path: str, query: SecretStr, key: SecretStr | None
    ) -> RESTResponse:
        import threading
        from decimal import Decimal, InvalidOperation

        params = parse_qs(query.get_secret_value(), keep_blank_values=True)
        selected = scope.get()
        entry = (
            method == "POST" and path == "/fapi/v1/order" and params.get("reduceOnly") != ["true"]
        )
        exit_post = method == "POST" and path == "/fapi/v1/order" and not entry
        regular_cancel = method == "DELETE" and path == "/fapi/v1/order"
        close_key = method == "DELETE" and path == "/fapi/v1/listenKey"
        with self.lock:
            now = self._time()
            cost = self.costs.get((method, path))
            if cost is None:
                self._deny("UNMODELED_ROUTE")
            if (
                path in {"/fapi/v1/openOrders", "/fapi/v1/openAlgoOrders", "/fapi/v3/positionRisk"}
                and "symbol" in params
            ):
                self._deny("GLOBAL_QUERY_REQUIRED")
            protected = (
                self.phase == "ENTRY_PROTECTION" and self.entry_owner == threading.get_ident()
            )
            if self.phase == "BOOTSTRAP" and (
                method != "GET" or path not in {"/fapi/v1/time", "/fapi/v1/exchangeInfo"}
            ):
                self._deny("BOOTSTRAP_SCOPE_INVALID")
            if selected == "RESCUE" and (method, path) not in {
                ("GET", "/fapi/v1/time"),
                ("GET", "/fapi/v3/positionRisk"),
                ("GET", "/fapi/v1/order"),
                ("DELETE", "/fapi/v1/order"),
                ("POST", "/fapi/v1/order"),
            }:
                self._deny("RESCUE_ROUTE_FORBIDDEN")
            if selected == "WATCH" and (method, path) not in {
                ("GET", "/fapi/v1/time"),
                ("GET", "/fapi/v3/positionRisk"),
                ("GET", "/fapi/v1/order"),
            }:
                self._deny("WATCH_ROUTE_FORBIDDEN")
            if (
                selected == "PROOF"
                and method != "GET"
                and (method, path) != ("DELETE", "/fapi/v1/algoOrder")
            ):
                self._deny("PROOF_WRITE_FORBIDDEN")
            if (
                selected in {"WATCH", "RESCUE"}
                and method == "GET"
                and path == "/fapi/v1/order"
                and params.get("origClientOrderId") != [self.owned_entry_id]
            ):
                self._deny("WATCH_ORDER_UNOWNED")
            if entry and (
                not self.synthetic
                or not protected
                or not self.admitted
                or self.entry_sent
                or self.stopped
                or params.get("type") != ["LIMIT"]
                or params.get("newClientOrderId") != [self.owned_entry_id]
            ):
                self._deny("NEW_ENTRY_FORBIDDEN")
            if entry:
                try:
                    quantity = Decimal(params.get("quantity", [""])[0])
                    bounded = quantity.is_finite() and 0 < quantity <= min(
                        self.owned_quantity, Decimal(".0007")
                    )
                except InvalidOperation:
                    bounded = False
                if (
                    not bounded
                    or params.get("symbol") != ["BTCUSDT"]
                    or params.get("side") != [self.owned_side]
                ):
                    self._deny("NEW_ENTRY_OWNERSHIP_OR_SIZE_INVALID")
            if regular_cancel:
                if params.get("origClientOrderId") != [self.owned_entry_id]:
                    self._deny("REGULAR_CANCEL_UNOWNED")
                if self.owned_entry_id in self.cancel_ids:
                    self.denied += 1
                    raise TPAdmissionStopped(
                        "Cancellation already attempted; query original intent"
                    )
            if exit_post:
                identifier = params.get("newClientOrderId", [""])[0]
                try:
                    qty = Decimal(params.get("quantity", [""])[0])
                    valid = qty.is_finite() and 0 < qty <= self.owned_quantity
                except (InvalidOperation, TypeError):
                    valid = False
                if (
                    not self.synthetic
                    or not self.entry_sent
                    or selected != "RESCUE"
                    or not self.owned_entry_id
                    or identifier != "DX-" + self.owned_entry_id[3:]
                    or identifier in self.exit_ids
                    or not valid
                    or params.get("symbol") != ["BTCUSDT"]
                    or params.get("type") != ["MARKET"]
                    or params.get("side") != ["SELL" if self.owned_side == "BUY" else "BUY"]
                ):
                    self._deny("REDUCE_ONLY_SCOPE_INVALID")
            # Original session/finalization syncs the clock before invoking the
            # fenced command. A local ordinary-budget stop must not prevent
            # reaching that command. This grants no non-clock ordinary request.
            rescue_clock = (method, path) == ("GET", "/fapi/v1/time") and (
                self.stopped or self.phase == "CLEANUP"
            )
            rescue = selected in {"RESCUE", "WATCH"} or close_key or rescue_clock
            if self.untrusted or (self.stopped and not rescue and selected != "PROOF"):
                self._deny("TRANSPORT_STOPPED")
            if not self._fresh(now) and (method, path) != ("GET", "/fapi/v1/time"):
                self._deny("WEIGHT_HEADER_STALE")
            reserve = (
                0
                if rescue or self.phase == "BOOTSTRAP"
                else (self.WATCH if self.flat_observed else self.RESCUE)
            )
            if self.phase == "PREPARATION" and not rescue:
                reserve += self.profile.entry
            if self._pressure(now) + cost + reserve > self.ceiling:
                if selected == "PROOF":
                    self.deferrals += 1
                    raise ProofDeferred
                self._deny("ROLLING_WEIGHT_RESERVE_REACHED")
            if protected and self.weights["ENTRY_PROTECTION"] + cost > self.profile.entry:
                self._deny("ENTRY_PROFILE_EXHAUSTED")
            if entry:
                self.entry_sent = True
            if regular_cancel:
                assert self.owned_entry_id is not None
                self.cancel_ids.add(self.owned_entry_id)
            if exit_post:
                self.exit_ids.add(identifier)
            self.attempts.append((now, cost))
            self.total_charge += cost
            self.peak = max(self.peak, sum(n for _, n in self.attempts))
            self.weights["CLEANUP" if close_key else self.phase] += cost
            self.forwarded += 1
            sequence = self.forwarded
            charge = self.total_charge
        try:
            response = self.sender(method, path, query, key)
        except Exception:
            with self.lock:
                self.untrusted = True
                self._mark("POSSIBLE_SEND_TRANSPORT_FAILURE")
            raise
        metadata = rate_headers(response.headers)
        with self.lock:
            value = metadata.get("x-mbx-used-weight-1m")
            if value is None:
                self.untrusted = True
                self._mark("WEIGHT_HEADER_MISSING")
            elif sequence >= self.header_sequence:
                self.header_weight, self.header_charge = int(value), charge
                self.header_at, self.header_sequence = self._time(), sequence
            if response.status in {418, 429}:
                self.untrusted = True
                self._mark("VENUE_RATE_LIMITED")
        return response

    def diagnostics(self) -> dict[str, Any]:
        result = super().diagnostics()
        result.update(
            {
                "tp_admission_candidate": "PRE_SEND_ROLLING_60_SECOND_GATE",
                "own_window_peak": self.peak,
                "wire_ceiling": self.ceiling,
                "window_seconds": self.WINDOW,
                "proof_deferrals": self.deferrals,
                "rescue_reserve": self.RESCUE,
                "watch_reserve": self.WATCH,
                "total_attempted_weight": self.total_charge,
                "minute_reset_credit": "OWN_ATTEMPT_EXPIRY_ONLY",
            }
        )
        return result
