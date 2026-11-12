"""Opt-in DEMO GET transport with durable cooperating-writer weight accounting.

This is the read side of an operational adapter, not a trading adapter. It never
opens an order writer session, grants account-order credit, or authorizes a mutation.
The caller owns its existing, independently anchored shared journal and client.
"""

import math
import os
import threading
import time
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from pydantic import SecretStr

from integrations.binance_usdm.demo import DemoSettings, Transport
from integrations.binance_usdm.demo_rate_limits import rate_headers
from integrations.binance_usdm.demo_tp_weight_model import MODELED_TP_ROUTE_COSTS
from integrations.binance_usdm.rest import NoRedirect, RESTResponse
from packages.persistence.demo_order_quota import LocalQuotaStopped, quota_require
from packages.persistence.demo_read_audit import AuditedDemoReadJournal
from packages.persistence.demo_shared_checkpoint import AnchoredSharedWeightJournal
from services.execution.demo_query_contract import require_wire_query
from services.execution.demo_runtime_cost_contract import require_runtime_cost_contract


@dataclass
class _ReadAttempt:
    method: str
    path: str
    query: SecretStr
    key: SecretStr | None
    sent: bool = False


_attempt: ContextVar[_ReadAttempt | None] = ContextVar("accounted_demo_read_attempt", default=None)


def _response_headers(raw: Any) -> dict[str, str]:
    pairs = list(raw.items())
    weights = [(name, value) for name, value in pairs if name.lower() == "x-mbx-used-weight-1m"]
    quota_require(len(weights) <= 1, "READ_TRANSPORT_WEIGHT_INVALID")
    if weights:
        value = weights[0][1]
        quota_require(
            isinstance(value, str) and value.isascii() and value.isdecimal() and len(value) <= 12,
            "READ_TRANSPORT_WEIGHT_INVALID",
        )
    return rate_headers(dict(pairs))


def _demo_get_http(method: str, path: str, query: SecretStr, key: SecretStr | None) -> RESTResponse:
    attempt = _attempt.get()
    quota_require(
        attempt is not None
        and not attempt.sent
        and method == attempt.method == "GET"
        and path == attempt.path
        and query is attempt.query
        and key is attempt.key,
        "READ_TRANSPORT_ACCOUNTED_ATTEMPT_REQUIRED",
    )
    assert attempt is not None
    AccountedDemoReadTransport._context()
    require_wire_query(method, path, query, key)
    attempt.sent = True
    headers = {"Accept": "application/json"}
    if key is not None:
        headers["X-MBX-APIKEY"] = key.get_secret_value()
    raw = query.get_secret_value()
    target = "https://demo-fapi.binance.com" + path + ("?" + raw if raw else "")
    request = Request(target, method="GET", headers=headers)
    try:
        with build_opener(NoRedirect()).open(request, timeout=5) as response:
            return RESTResponse(
                response.status, response.read(4_194_305), _response_headers(response.headers)
            )
    except HTTPError as error:
        try:
            return RESTResponse(error.code, error.read(4_194_305), _response_headers(error.headers))
        finally:
            error.close()
    except (URLError, OSError):
        raise LocalQuotaStopped("READ_TRANSPORT_NETWORK_IO_FAILED") from None


class AccountedDemoReadTransport:
    """Serialize this reader; reserve each GET in the existing shared store first.

    A successful send is completed only after its independent PG checkpoint is
    committed. A lost response poisons the shared store and retains its pending
    charge. Local refusals retain their reserved modeled charge for a full window.
    Construction is explicit; default refusal precedes provider access.
    """

    def __init__(
        self,
        settings: DemoSettings,
        shared: AnchoredSharedWeightJournal,
        *,
        enable_reads: bool = False,
        sender: Transport = _demo_get_http,
        monotonic: Callable[[], float] = time.monotonic,
        max_requests: int = 48,
        max_seconds: float = 75.0,
    ):
        quota_require(enable_reads is True, "READ_TRANSPORT_DISABLED")
        quota_require(
            isinstance(settings, DemoSettings)
            and settings.mode == "DEMO"
            and not settings.demo_trading_enabled
            and not settings.live_trading_enabled
            and settings.margin_mode == "ISOLATED"
            and settings.symbols == ("BTCUSDT",),
            "READ_TRANSPORT_SCOPE_INVALID",
        )
        self._context()
        quota_require(callable(sender) and callable(monotonic), "READ_TRANSPORT_WIRING_INVALID")
        quota_require(
            isinstance(shared, AnchoredSharedWeightJournal) and not shared.read_only,
            "READ_TRANSPORT_ANCHORED_WRITABLE_PROVIDER_REQUIRED",
        )
        quota_require(
            type(max_requests) is int
            and 1 <= max_requests <= 48
            and type(max_seconds) in {int, float}
            and math.isfinite(max_seconds)
            and 0 < max_seconds <= 75,
            "READ_TRANSPORT_BOUNDS_INVALID",
        )
        require_runtime_cost_contract(MODELED_TP_ROUTE_COSTS)
        self._settings = settings.model_copy(deep=True)
        self._settings_snapshot = settings.model_copy(deep=True)
        self._shared = shared
        self._provider = self._binding()
        self._sender = self._configured_sender = sender
        self._clock = self._configured_clock = monotonic
        self._costs = dict(MODELED_TP_ROUTE_COSTS)
        self._max_requests, self._max_seconds = max_requests, float(max_seconds)
        self._bounds = (self._max_requests, self._max_seconds)
        self._started = self._last = self._sample()
        self._lock = threading.RLock()
        self._closed = self._stopped = False
        self._http_attempts = self._reservations = self._completed = self._modeled_cost = 0
        self._denied = 0

    @staticmethod
    def _context() -> None:
        from integrations.binance_usdm import demo

        quota_require(
            demo.REST_HOST == "https://demo-fapi.binance.com", "READ_TRANSPORT_DEMO_HOST_REQUIRED"
        )
        quota_require(
            os.getenv("TRADING_MODE", "PAPER").upper() != "LIVE"
            and os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "false"
            and not any(
                os.getenv(name)
                for name in (
                    "BINANCE_API_KEY",
                    "BINANCE_API_SECRET",
                    "BINANCE_LIVE_API_KEY",
                    "BINANCE_LIVE_API_SECRET",
                )
            ),
            "READ_TRANSPORT_LIVE_OR_MIXED_CONTEXT",
        )

    def _binding(self) -> tuple[Any, ...]:
        shared = self._shared
        return (
            id(shared),
            shared.scope,
            shared.identity,
            shared.directory,
            shared.path,
            shared.lock_path,
            shared.db_inode,
            shared.lock_inode,
            shared.read_only,
            id(shared.anchors),
            id(shared.anchors.engine),
            id(shared.anchors.engine.pool),
            id(shared.monotonic),
            id(shared.wall_clock),
            id(shared.boot),
            id(shared.audit) if isinstance(shared, AuditedDemoReadJournal) else None,
            id(shared.audit.engine) if isinstance(shared, AuditedDemoReadJournal) else None,
        )

    def _sample(self) -> float:
        try:
            value = self._clock()
        except Exception:
            raise LocalQuotaStopped("READ_TRANSPORT_CLOCK_INVALID") from None
        quota_require(
            type(value) in {int, float} and math.isfinite(value) and value >= 0,
            "READ_TRANSPORT_CLOCK_INVALID",
        )
        return value

    def _check(self, *, admission: bool = True) -> None:
        quota_require(not self._closed and not self._stopped, "READ_TRANSPORT_CLOSED_OR_STOPPED")
        self._context()
        quota_require(
            self._sender is self._configured_sender
            and self._clock is self._configured_clock
            and self._settings == self._settings_snapshot
            and self._binding() == self._provider
            and (self._max_requests, self._max_seconds) == self._bounds,
            "READ_TRANSPORT_BINDING_CHANGED",
        )
        require_runtime_cost_contract(self._costs)
        now = self._sample()
        quota_require(now >= self._last, "READ_TRANSPORT_CLOCK_INVALID")
        self._last = now
        quota_require(now - self._started < self._max_seconds, "READ_TRANSPORT_DEADLINE")
        if admission:
            quota_require(self._http_attempts < self._max_requests, "READ_TRANSPORT_REQUEST_LIMIT")

    @staticmethod
    def _weight(response: RESTResponse) -> int | None:
        values = [v for k, v in response.headers.items() if k.lower() == "x-mbx-used-weight-1m"]
        quota_require(len(values) <= 1, "READ_TRANSPORT_WEIGHT_INVALID")
        if not values:
            return None
        value = values[0]
        quota_require(
            isinstance(value, str) and value.isascii() and value.isdecimal() and len(value) <= 12,
            "READ_TRANSPORT_WEIGHT_INVALID",
        )
        return int(value)

    def _poison(self) -> None:
        self._stopped = True
        try:
            self._shared.stop_unknown()
        except Exception:
            # A commit gap stays blocked by the independent checkpoint. No retry.
            raise LocalQuotaStopped("READ_TRANSPORT_CHECKPOINT_OR_STOP_FAILED") from None

    def __call__(
        self, method: str, path: str, query: SecretStr, key: SecretStr | None
    ) -> RESTResponse:
        with self._lock:
            try:
                self._check()
                # No PUT/POST/DELETE, including listen-key and reduce-only paths.
                quota_require(method == "GET", "READ_TRANSPORT_MUTATION_FORBIDDEN")
                require_wire_query(method, path, query, key)
                cost = self._costs.get((method, path))
                quota_require(type(cost) is int and cost > 0, "READ_TRANSPORT_ROUTE_UNMODELED")
                assert isinstance(cost, int)
                token = (
                    self._shared.reserve_read(path, cost)
                    if isinstance(self._shared, AuditedDemoReadJournal)
                    else self._shared.reserve(cost, "ORDINARY")
                )
            except Exception as error:
                self._denied += 1
                self._stopped = True
                if isinstance(error, LocalQuotaStopped):
                    raise
                raise LocalQuotaStopped("READ_TRANSPORT_ADMISSION_FAILED") from None
            self._reservations += 1
            self._modeled_cost += cost
            try:
                self._check()
            except Exception:
                self._denied += 1
                self._stopped = True
                try:
                    self._shared.finish(token, used_weight=None, status=None, sent=False)
                except Exception:
                    raise LocalQuotaStopped("READ_TRANSPORT_CHECKPOINT_OR_STOP_FAILED") from None
                raise
            self._http_attempts += 1
            context = _attempt.set(_ReadAttempt(method, path, query, key))
            try:
                response = self._sender(method, path, query, key)
            except Exception:
                self._poison()
                raise LocalQuotaStopped("READ_TRANSPORT_RESPONSE_UNKNOWN") from None
            finally:
                _attempt.reset(context)
            finished = False
            try:
                quota_require(
                    isinstance(response, RESTResponse)
                    and type(response.status) is int
                    and 100 <= response.status <= 599
                    and isinstance(response.body, bytes)
                    and len(response.body) <= 4_194_304,
                    "READ_TRANSPORT_RESPONSE_INVALID",
                )
                if path == "/fapi/v1/ticker/bookTicker":
                    # Its header is nonauthoritative. Keep the documented charge.
                    self._shared.finish_unobserved(token, status=response.status)
                    finished = True
                else:
                    weight = self._weight(response)
                    self._shared.finish(token, used_weight=weight, status=response.status)
                    finished = True
                    if response.status not in {418, 429}:
                        quota_require(weight is not None, "READ_TRANSPORT_WEIGHT_UNAVAILABLE")
                quota_require(
                    response.status not in {418, 429}, "READ_TRANSPORT_RATE_LIMITED_NO_RETRY"
                )
                quota_require(200 <= response.status < 300, "READ_TRANSPORT_HTTP_ERROR_NO_RETRY")
                self._check(admission=False)
            except Exception as error:
                self._stopped = True
                # finish may have committed a shared stop, or an append may have
                # failed after SQLite. Do not guess a new checkpoint or refund.
                if not finished:
                    self._poison()
                if isinstance(error, LocalQuotaStopped):
                    raise
                raise LocalQuotaStopped("READ_TRANSPORT_COMPLETION_FAILED") from None
            self._completed += 1
            return response

    def close(self) -> None:
        with self._lock:
            self._closed = True

    def diagnostics(self) -> dict[str, Any]:
        with self._lock:
            return dict(
                read_transport="ANCHORED_DEMO_GET_ONLY",
                http_attempts=self._http_attempts,
                reserved_requests=self._reservations,
                completed_reads=self._completed,
                read_audit_attached=isinstance(self._shared, AuditedDemoReadJournal),
                modeled_weight_charged=self._modeled_cost,
                denied_requests=self._denied,
                closed=self._closed,
                stopped=self._stopped,
                max_requests=self._max_requests,
                max_seconds=self._max_seconds,
                scope="COOPERATING_WRITERS_WITH_SHARED_STORE_ONLY",
                account_order_usage_verified=False,
                actual_ip_capacity_reserved=False,
                request_cost_upper_bounds_proven=False,
                financial_proof_verified=False,
                order_writes=0,
                execution_approved=False,
                orders_enabled=False,
                live_trading_enabled=False,
            )
