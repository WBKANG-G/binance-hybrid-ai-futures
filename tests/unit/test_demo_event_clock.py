"""Verified exchange-clock bounds, strict age limits, and finite local waits."""

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

import services.execution.demo_event_clock as clock
from integrations.binance_usdm.demo import DemoClient, DemoClockSample, DemoCredentials, DemoFailure
from services.execution.demo_models import PrivateSessionDiagnostic
from tests.integration.test_demo import settings


def client(sample=None, now=1020):
    state = {"now": now}
    return SimpleNamespace(
        clock_ms=lambda: state["now"],
        clock_sample=sample or DemoClockSample(1000, 1020, 1016),
        synced_at=1020,
        cooldown_until=0,
    ), state


@pytest.mark.parametrize("offset", [-500, -6, 0, 6, 500])
def test_same_exchange_instant_uses_verified_offset_not_pc_point(offset):
    c, _ = client(DemoClockSample(1000, 1000, 1000 + offset))
    detail = PrivateSessionDiagnostic()
    assert clock.validate_event_time(c, 1020 + offset, detail) == 0
    assert detail.event_clock_offset_ms == offset and detail.event_clock_rtt_ms == 0
    assert detail.event_local_age_ms == -offset and detail.event_clock_wait_ms == 0


def test_positive_six_ms_verified_offset_passes_without_timestamp_rewrite():
    c, _ = client(DemoClockSample(1000, 1000, 1006))
    stamp = 1026
    detail = PrivateSessionDiagnostic()
    assert clock.validate_event_time(c, stamp, detail) == 0 and stamp == 1026
    assert detail.event_local_age_ms == -6 and detail.event_clock_offset_ms == 6


def test_uncertainty_is_resolved_by_waiting_until_lower_bound_reaches_event(monkeypatch):
    c, state = client()
    detail = PrivateSessionDiagnostic()
    waits = []

    def sleep(delay):
        waits.append(delay)
        state["now"] += round(delay * 1000)

    monkeypatch.setattr(clock, "_sleep", sleep)
    # Point estimate is 1026; E1028 is inside [1016,1036]. Wait12ms,
    # then ALL possible server clocks are >=E, worst-case age20ms.
    assert clock.validate_event_time(c, 1028, detail) == 20
    assert waits == [0.012] and detail.event_clock_wait_ms == 12
    assert detail.event_clock_rtt_ms == 20 and detail.event_clock_offset_ms == 6


def test_async_wait_uses_same_conservative_bounds(monkeypatch):
    c, state = client()
    detail = PrivateSessionDiagnostic()

    async def pause(delay):
        state["now"] += round(delay * 1000)
        await asyncio.sleep(0)

    monkeypatch.setattr(clock, "_pause", pause)
    assert asyncio.run(clock.validate_event_time_async(c, 1028, detail)) == 20
    assert detail.event_clock_wait_ms == 12


@pytest.mark.parametrize("stamp", [1037, 1036 - 30001])
def test_definite_future_and_old_event_are_rejected_without_waiting(monkeypatch, stamp):
    c, _ = client()
    waits = []
    monkeypatch.setattr(clock, "_sleep", lambda delay: waits.append(delay))
    with pytest.raises(DemoFailure, match="Stale/future"):
        clock.validate_event_time(c, stamp, PrivateSessionDiagnostic())
    assert not waits


def test_worst_case_age_boundary_remains_exactly_thirty_seconds():
    c, _ = client()
    assert clock.validate_event_time(c, 1036 - 30000, PrivateSessionDiagnostic()) == 30000


@pytest.mark.parametrize("stamp", [None, True, "1020", 1.5])
def test_invalid_timestamp_type_is_not_reinterpreted(stamp):
    c, _ = client()
    with pytest.raises(DemoFailure, match="Stale/future"):
        clock.validate_event_time(c, stamp, PrivateSessionDiagnostic())


@pytest.mark.parametrize(
    "change", ["unsynced", "missing_sample", "old", "backward", "rtt", "offset", "cooldown"]
)
def test_untrusted_or_stale_probe_cannot_authorize_event(change):
    c, state = client()
    if change == "unsynced":
        c.synced_at = None
    elif change == "missing_sample":
        c.clock_sample = None
    elif change == "old":
        state["now"] = 61021
    elif change == "backward":
        state["now"] = 1019
    elif change == "rtt":
        c.clock_sample = DemoClockSample(1000, 2001, 1500)
    elif change == "offset":
        c.clock_sample = DemoClockSample(1000, 1000, 2001)
    else:
        c.cooldown_until = 1021
    with pytest.raises(DemoFailure):
        clock.validate_event_time(c, 1020, PrivateSessionDiagnostic())


def test_frozen_clock_wait_is_bounded_and_does_not_accept_future(monkeypatch):
    c, _ = client()
    waits = []
    monkeypatch.setattr(clock, "_sleep", lambda delay: waits.append(delay))
    with pytest.raises(DemoFailure, match="untrusted"):
        clock.validate_event_time(c, 1028, PrivateSessionDiagnostic())
    assert len(waits) == 3 and sum(waits) <= 1


def test_clock_jump_during_wait_rechecks_age(monkeypatch):
    c, state = client()
    monkeypatch.setattr(clock, "_sleep", lambda delay: state.update(now=41020))
    with pytest.raises(DemoFailure, match="Stale/future"):
        clock.validate_event_time(c, 1028, PrivateSessionDiagnostic())


def test_sdk_preserves_probe_only_after_original_trust_checks():
    now = [1000]
    calls = []
    from tests.fixtures.demo import FakeDemo

    def send(method, path, query, key):
        calls.append((method, path))
        now[0] = 1020
        return FakeDemo.response({"serverTime": 1016})

    c = DemoClient(
        settings(),
        DemoCredentials(SecretStr("synthetic"), SecretStr("synthetic")),
        send=send,
        clock_ms=lambda: now[0],
    )
    c.sync_clock()
    assert c.offset == 6 and c.synced_at == 1020
    assert c.clock_sample == DemoClockSample(1000, 1020, 1016)
    assert calls == [("GET", "/fapi/v1/time")]


@pytest.mark.parametrize("after,server", [(2001, 1500), (1000, 2001)])
def test_sdk_rejects_bad_probe_and_clears_saved_sample(after, server):
    from tests.fixtures.demo import FakeDemo

    now = [1000]

    def send(method, path, query, key):
        now[0] = after
        return FakeDemo.response({"serverTime": server})

    c = DemoClient(
        settings(),
        DemoCredentials(SecretStr("synthetic"), SecretStr("synthetic")),
        send=send,
        clock_ms=lambda: now[0],
    )
    with pytest.raises(DemoFailure):
        c.sync_clock()
    assert c.clock_sample is None and c.synced_at is None


def test_cancelling_unproven_clock_wait_is_failure_not_normal_socket_shutdown(monkeypatch):
    from services.execution.demo_event_inbox import EventInbox

    c, _ = client()

    async def run():
        entered = asyncio.Event()

        async def pause(delay):
            entered.set()
            await asyncio.Future()

        monkeypatch.setattr(clock, "_pause", pause)

        class Socket:
            async def recv(self):
                return '{"e":"ALGO_UPDATE","E":1028}'

        failures = []
        inbox = EventInbox(
            lambda: 1020,
            lambda: failures.append(True),
            validate_time=lambda stamp, detail: clock.validate_event_time_async(c, stamp, detail),
        )
        task = asyncio.create_task(inbox.run(Socket()))
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert failures == [True] and inbox.failure.code == "EVENT_CLOCK_INCOMPLETE"
        with pytest.raises(DemoFailure):
            await inbox.take()

    asyncio.run(run())
