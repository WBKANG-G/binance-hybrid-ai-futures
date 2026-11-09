"""Observational synthetic lifecycle cost audit; never logs query, keys or bodies.

This fixture does not grant, reserve, wait, retry, or change any SDK response.
It is not wired into a CLI or production execution path.
"""

import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from pydantic import SecretStr

from integrations.binance_usdm.demo import Transport
from integrations.binance_usdm.rest import RESTResponse

PHASES = frozenset(
    {
        "BOOTSTRAP",
        "SESSION_OTHER",
        "SESSION_READY",
        "EARLY_CHECK",
        "RESUME",
        "PREPARATION",
        "RESERVATION",
        "SUBMISSION_PROTECTION",
        "OBSERVATION",
        "FINALIZATION",
        "FLAT_CONFIRMATION",
    }
)


class PhaseAudit:
    def __init__(
        self, sender: Transport, costs: Mapping[tuple[str, str], int], *, trace_cap: int = 5000
    ):
        if type(trace_cap) is not int or not 1 <= trace_cap <= 10000:
            raise ValueError("Invalid synthetic trace cap")
        if any(type(v) is not int or v < 0 for v in costs.values()):
            raise ValueError("Explicit nonnegative modeled costs required")
        self.sender = sender
        self.costs = dict(costs)
        self.trace_cap = trace_cap
        self._stack: ContextVar[tuple[str, ...]] = ContextVar(
            "synthetic_lifecycle_phase", default=()
        )
        self._lock = threading.Lock()
        self._finalizing = False
        self._rows: list[dict[str, Any]] = []
        self._counts: dict[str, int] = {}
        self._weights: dict[str, int] = {}
        self._unmodeled = self._attempts = 0

    @contextmanager
    def span(self, phase: str) -> Iterator[None]:
        if phase not in PHASES:
            raise ValueError("Unknown audit phase")
        token = self._stack.set((*self._stack.get(), phase))
        try:
            yield
        finally:
            self._stack.reset(token)

    def instrument[**P, R](self, phase: str, function: Callable[P, R]) -> Callable[P, R]:
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
            with self.span(phase):
                return function(*args, **kwargs)

        return wrapped

    def begin_finalization(self) -> None:
        with self._lock:
            self._finalizing = True

    def _phase(self) -> str:
        stack = self._stack.get()
        if "FLAT_CONFIRMATION" in stack:
            return "FLAT_CONFIRMATION"
        if self._finalizing:
            return "FINALIZATION"
        if "OBSERVATION" in stack:
            return "OBSERVATION"
        return stack[-1] if stack else "SESSION_OTHER"

    def _record(
        self, method: str, path: str, status: int | None, *, phase: str | None = None
    ) -> None:
        with self._lock:
            current = phase or self._phase()
            cost = self.costs.get((method, path))
            self._attempts += 1
            self._counts[current] = self._counts.get(current, 0) + 1
            if cost is None:
                self._unmodeled += 1
            else:
                self._weights[current] = self._weights.get(current, 0) + cost
            if len(self._rows) < self.trace_cap:
                self._rows.append(
                    {
                        "attempt": self._attempts,
                        "phase": current,
                        "method": method if method in {"GET", "POST", "PUT", "DELETE"} else "OTHER",
                        "path": path if cost is not None else "UNMODELED_ROUTE",
                        "status": status,
                        "assumed_weight": cost,
                    }
                )

    def import_bootstrap(self, rows: list[dict[str, Any]]) -> None:
        """Import only the allowlisted method/path/status from prior synthetic calls."""
        for row in rows:
            self._record(str(row["method"]), str(row["path"]), row.get("status"), phase="BOOTSTRAP")

    def __call__(
        self, method: str, path: str, query: SecretStr, api_key: SecretStr | None
    ) -> RESTResponse:
        status = None
        with self._lock:
            phase = self._phase()
        try:
            response = self.sender(method, path, query, api_key)
            status = response.status
            return response
        finally:
            self._record(method, path, status, phase=phase)

    def report(self) -> dict[str, Any]:
        with self._lock:
            return {
                "actual_venue_used": False,
                "assumed_weights_are_not_proven_bounds": True,
                "attempts": self._attempts,
                "known_assumed_total_weight": sum(self._weights.values()),
                "phase_attempts": dict(self._counts),
                "phase_assumed_weights": dict(self._weights),
                "unmodeled_attempts": self._unmodeled,
                "complete_cost_model": self._unmodeled == 0,
                "trace_truncated": len(self._rows) != self._attempts,
                "trace": list(self._rows),
            }
