"""Bounded receipt validation and coalescing of REST-invalidation hints.

No fill arithmetic or account state is derived from these messages. Every frame
is checked; only known, fresh hints share one later strict REST reconciliation.
"""

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from integrations.binance_usdm.demo import DemoFailure
from services.execution.demo_models import PrivateSessionDiagnostic
from services.execution.demo_session_diagnostics import (
    EVENT_KINDS,
    EventKind,
    event_info,
    failure_diagnostic,
)

MAX_PENDING_EVENTS = 128


class Receiver(Protocol):
    async def recv(self) -> str | bytes: ...


@dataclass(frozen=True)
class ReceivedHint:
    kind: EventKind
    stamp: int
    receive_age_ms: int
    clock_wait_ms: int = 0

    def event(self) -> dict[str, Any]:
        return {"e": self.kind, "E": self.stamp}


class EventInbox:
    def __init__(
        self,
        now_ms: Callable[[], int],
        failed: Callable[[], None],
        *,
        validate_time: Callable[[object, PrivateSessionDiagnostic], Awaitable[int]] | None = None,
    ):
        self.now_ms, self.failed = now_ms, failed
        self.validate_time = validate_time
        self.wakeup = asyncio.Event()
        self.urgent = asyncio.Event()
        self.latest: ReceivedHint | None = None
        self.pending = 0
        self.received = 0
        self.coalesced = 0
        self.failure: PrivateSessionDiagnostic | None = None

    def raise_if_failed(self) -> None:
        if self.failure is not None:
            raise DemoFailure("DEMO private receiver failed; entries blocked")

    async def run(self, websocket: Receiver) -> None:
        detail = PrivateSessionDiagnostic(stage="RECEIVE")
        try:
            while True:
                detail = PrivateSessionDiagnostic(stage="RECEIVE")
                message = await websocket.recv()
                self.received = min(1000000000, self.received + 1)
                if len(message) > 1048576:
                    raise DemoFailure("DEMO private event oversized")
                detail.stage = "PARSE"

                def reject_constant(value: str) -> Any:
                    raise ValueError("Non-finite event")

                raw = json.loads(message, parse_float=Decimal, parse_constant=reject_constant)
                if not isinstance(raw, dict):
                    raise DemoFailure("Invalid DEMO event object")
                now = self.now_ms()
                detail.event_kind, detail.event_age_ms = event_info(raw, now)
                detail.event_receive_age_ms = detail.event_age_ms
                detail.stage = "EVENT"
                kind = raw.get("e")
                if not isinstance(kind, str) or kind not in EVENT_KINDS:
                    raise DemoFailure("Unrecognized DEMO private event")
                if kind == "listenKeyExpired":
                    raise DemoFailure("DEMO private listen key expired")
                stamp = raw.get("E")
                if self.validate_time is not None:
                    try:
                        age = await self.validate_time(stamp, detail)
                    except asyncio.CancelledError:
                        self.failure = failure_diagnostic(
                            DemoFailure("DEMO event clock validation interrupted"), detail
                        )
                        self.failed()
                        self.urgent.set()
                        self.wakeup.set()
                        raise
                else:
                    if type(stamp) is not int or not 0 <= now - stamp <= 30000:
                        raise DemoFailure("Stale/future DEMO private event")
                    age = now - stamp
                if type(stamp) is not int:
                    raise DemoFailure("Stale/future DEMO private event")
                detail.event_receive_age_ms = age
                if self.pending >= MAX_PENDING_EVENTS:
                    raise DemoFailure("DEMO private event inbox overflow")
                hint = ReceivedHint(detail.event_kind, stamp, age, detail.event_clock_wait_ms)
                self.pending += 1
                if self.latest is None or stamp >= self.latest.stamp:
                    self.latest = hint
                if kind != "ACCOUNT_UPDATE":
                    self.urgent.set()
                self.wakeup.set()
        except Exception as error:
            self.failure = failure_diagnostic(error, detail)
            self.failed()  # In-memory admission gate; never races DB cleanup.
            self.urgent.set()
            self.wakeup.set()

    async def take(
        self,
        timeout: float = 5,
        *,
        before_coalesce: Callable[[], Awaitable[float]] | None = None,
    ) -> ReceivedHint:
        self.raise_if_failed()
        await asyncio.wait_for(self.wakeup.wait(), timeout=timeout)
        self.raise_if_failed()
        # Only the opt-in protected-observation candidate supplies this hook.
        # The deadline starts at wakeup, not after invalidation or the last frame.
        deadline = asyncio.get_running_loop().time()
        if before_coalesce is not None and not self.urgent.is_set():
            delay = await before_coalesce()
            self.raise_if_failed()
            if not 0 <= delay <= 0.25:
                raise DemoFailure("Invalid DEMO event batch delay")
            remaining = deadline + delay - asyncio.get_running_loop().time()
            if remaining > 0 and not self.urgent.is_set():
                try:
                    await asyncio.wait_for(self.urgent.wait(), timeout=remaining)
                except TimeoutError:
                    pass
            self.raise_if_failed()
        hint = self.latest
        if hint is None:
            raise DemoFailure("DEMO private event inbox empty")
        self.coalesced = min(1000000000, self.coalesced + max(0, self.pending - 1))
        self.latest = None
        self.pending = 0
        self.wakeup.clear()
        self.urgent.clear()
        return hint
