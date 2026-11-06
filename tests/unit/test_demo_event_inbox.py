"""Fresh receipt, bounded coalescing, and no masking of a single unsafe frame."""

import asyncio
import json

import pytest

from integrations.binance_usdm.demo import DemoFailure
from services.execution.demo_event_inbox import MAX_PENDING_EVENTS, EventInbox


class Source:
    def __init__(self, frames):
        self.queue = asyncio.Queue()
        for frame in frames:
            self.queue.put_nowait(frame)

    async def recv(self):
        return await self.queue.get()


def event(stamp=2000, kind="ACCOUNT_UPDATE"):
    return json.dumps({"e": kind, "E": stamp, "private": "MUST_NOT_RETAIN"})


async def ready(frames, now=2000):
    failures = []
    inbox = EventInbox(lambda: now, lambda: failures.append(True))
    task = asyncio.create_task(inbox.run(Source(frames)))
    await asyncio.sleep(0)
    return inbox, task, failures


async def close(task):
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def test_only_latest_known_fresh_hint_is_kept_and_rest_payload_not_retained():
    async def run():
        inbox, task, failures = await ready(
            [event(1000), event(2000, "ORDER_TRADE_UPDATE"), event(1500)]
        )
        hint = await inbox.take()
        assert hint.stamp == 2000 and hint.kind == "ORDER_TRADE_UPDATE"
        assert hint.event() == {"e": "ORDER_TRADE_UPDATE", "E": 2000}
        assert inbox.received == 3 and inbox.coalesced == 2 and inbox.pending == 0
        assert inbox.latest is None and not failures
        await close(task)
        assert not failures

    asyncio.run(run())


@pytest.mark.parametrize(
    "bad,code",
    [
        (event(-28001), "EVENT_TIME_INVALID"),
        (event(2001), "EVENT_TIME_INVALID"),
        (event(kind="PRIVATE_UNKNOWN"), "EVENT_UNKNOWN"),
        ("[]", "EVENT_OBJECT_INVALID"),
        ("NaN", "EVENT_JSON_INVALID"),
        ('{"e":"ACCOUNT_UPDATE","E":true}', "EVENT_TIME_INVALID"),
        ('{"e":"listenKeyExpired"}', "LISTEN_KEY_EXPIRED"),
    ],
)
def test_invalid_frame_cannot_be_hidden_between_valid_hints(bad, code):
    async def run():
        inbox, task, failures = await ready([event(), bad, event()])
        await task
        with pytest.raises(DemoFailure):
            await inbox.take()
        assert failures == [True] and inbox.failure.code == code
        assert "PRIVATE_UNKNOWN" not in inbox.failure.model_dump_json()

    asyncio.run(run())


def test_pending_limit_fails_closed_instead_of_unbounded_buffer_or_silent_drop():
    async def run():
        inbox, task, failures = await ready([event()] * (MAX_PENDING_EVENTS + 1))
        await task
        assert inbox.pending == MAX_PENDING_EVENTS and inbox.received == MAX_PENDING_EVENTS + 1
        assert inbox.failure.code == "EVENT_BUFFER_OVERFLOW" and failures == [True]
        with pytest.raises(DemoFailure):
            await inbox.take()

    asyncio.run(run())


def test_idle_timeout_does_not_cancel_receiver_or_create_failure():
    async def run():
        inbox, task, failures = await ready([])
        with pytest.raises(TimeoutError):
            await inbox.take(timeout=0.001)
        assert not task.done() and not failures
        await close(task)

    asyncio.run(run())
