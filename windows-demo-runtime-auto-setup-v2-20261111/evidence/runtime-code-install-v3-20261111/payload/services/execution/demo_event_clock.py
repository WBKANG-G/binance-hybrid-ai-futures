"""Conservative event time using an already verified server-time probe.

For local send b, receive a and server stamp s, offset lies in [s-a,s-b].
Never admit an event beyond the upper bound. Within that uncertainty, wait
until the lower bound reaches E; require upper-E <= 30 seconds throughout.
No network calls, timestamp rewriting, or additive acceptance tolerance.
"""

import asyncio
import time
from dataclasses import dataclass, field

from integrations.binance_usdm.demo import DemoClient, DemoClockSample, DemoFailure
from services.execution.demo_models import PrivateSessionDiagnostic

_sleep = time.sleep


@dataclass(frozen=True)
class EventTime:
    age_ms: int
    wait_ms: int


def assess(client: DemoClient, stamp: object, detail: PrivateSessionDiagnostic) -> EventTime:
    if type(stamp) is not int:
        raise DemoFailure("Stale/future DEMO private event")
    now = client.clock_ms()
    sample = client.clock_sample
    if (
        not isinstance(sample, DemoClockSample)
        or type(now) is not int
        or any(type(v) is not int for v in (sample.before_ms, sample.after_ms, sample.server_ms))
        or client.synced_at is None
    ):
        raise DemoFailure("DEMO event clock unavailable or untrusted")
    rtt = sample.after_ms - sample.before_ms
    offset = sample.server_ms - (sample.before_ms + sample.after_ms) // 2
    if not 0 <= rtt <= 1000 or abs(offset) > 1000:
        raise DemoFailure("DEMO event clock unavailable or untrusted")
    if not 0 <= now - sample.after_ms <= 60000 or now < client.cooldown_until:
        raise DemoFailure("Clock stale or cooldown active")
    lower = now + sample.server_ms - sample.after_ms
    upper = now + sample.server_ms - sample.before_ms
    age = upper - stamp
    detail.event_clock_offset_ms = offset
    detail.event_clock_rtt_ms = rtt
    detail.event_local_age_ms = max(-60000, min(3600000, now - stamp))
    detail.event_age_ms = max(-60000, min(3600000, age))
    if stamp > upper or age > 30000:
        raise DemoFailure("Stale/future DEMO private event")
    return EventTime(age, max(0, stamp - lower))


@dataclass
class WaitBudget:
    started: float = field(default_factory=time.monotonic)
    waits: int = 0
    requested_ms: int = 0

    def delay(self, check: EventTime, detail: PrivateSessionDiagnostic) -> float:
        # At most the existing 1-second RTT budget; finite local waits only.
        if (
            self.waits >= 3
            or self.requested_ms + check.wait_ms > 1000
            or time.monotonic() - self.started + check.wait_ms / 1000 > 1
        ):
            raise DemoFailure("DEMO event clock unavailable or untrusted")
        self.waits += 1
        self.requested_ms += check.wait_ms
        detail.event_clock_wait_ms = min(2000, detail.event_clock_wait_ms + check.wait_ms)
        return check.wait_ms / 1000

    def finish_wait(self) -> None:
        if time.monotonic() - self.started > 1:
            raise DemoFailure("DEMO event clock unavailable or untrusted")


def validate_event_time(client: DemoClient, stamp: object, detail: PrivateSessionDiagnostic) -> int:
    budget = WaitBudget()
    while True:
        check = assess(client, stamp, detail)
        if not check.wait_ms:
            return check.age_ms
        _sleep(budget.delay(check, detail))
        budget.finish_wait()


async def _pause(seconds: float) -> None:
    await asyncio.sleep(seconds)


async def validate_event_time_async(
    client: DemoClient, stamp: object, detail: PrivateSessionDiagnostic
) -> int:
    budget = WaitBudget()
    while True:
        check = assess(client, stamp, detail)
        if not check.wait_ms:
            return check.age_ms
        await _pause(budget.delay(check, detail))
        budget.finish_wait()
