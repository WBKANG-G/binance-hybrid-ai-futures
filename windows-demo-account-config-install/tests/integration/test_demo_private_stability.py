"""Real PG and localhost WSS; accelerated fixture clock drives original 25m renewal."""

import asyncio
import json
import time as real_time
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from services.execution import demo_private_stability as module
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401


@pytest.fixture
def case(preview_case):  # noqa: F811
    store, c, fake, _, _ = preview_case
    old = c.client
    c.client = module.StabilityClient(
        old.settings.model_copy(update={"demo_trading_enabled": True}),
        old.credentials,
        send=old.send,
        clock_ms=old.clock_ms,
    )
    c.client.sync_clock()
    store.change(c.lease, "TEST_STABILITY_PAUSE", lambda ledger: setattr(ledger, "paused", True))
    return store, c, fake


async def exercise(case, *, duration=0.03, accelerate=False, bad_frame=False):
    _, c, fake = case
    original_clock = c.client.sync_clock
    if accelerate:
        original = c.client.sync_clock

        def fast_clock():
            fake.at += timedelta(seconds=10)
            original()

        c.client.sync_clock = fast_clock

    async def handler(socket):
        if bad_frame:
            await socket.send(
                json.dumps({"e": "PRIVATE_UNKNOWN_PAYLOAD", "E": int(fake.at.timestamp() * 1000)})
            )
            await socket.wait_closed()
        elif accelerate:
            try:
                while True:
                    await socket.send(
                        json.dumps({"e": "ACCOUNT_UPDATE", "E": int(fake.at.timestamp() * 1000)})
                    )
                    await asyncio.sleep(0.01)
            except Exception:
                pass
        else:
            await socket.wait_closed()

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]

        @asynccontextmanager
        async def connector(url):
            async with connect(f"ws://127.0.0.1:{port}") as socket:
                yield socket

        # The accelerated account clock still traverses the original25-minute
        # renewal interval. Do not require150 sync calls to fit inside5 real
        # seconds on every CI machine. Hold only this observer's duration clock
        # below completion until a genuine renewal succeeds; a real30s outer
        # watchdog still fails if renewal never happens. AsyncIO time is untouched.
        first = []
        created = []
        renewal_elapsed = []
        original_key = c.client.listen_key

        def observe_clock():
            now = real_time.monotonic()
            if not first:
                first.append(now)
            return min(now, first[0] + duration - 0.001) if not c.client.key_renewals else now

        def observe_key(method):
            if method == "POST":
                created.append(fake.at)
            if method == "PUT" and created:
                renewal_elapsed.append(int((fake.at - created[-1]).total_seconds() * 1000))
            return original_key(method)

        try:
            if accelerate:
                with (
                    patch.object(module, "time", SimpleNamespace(monotonic=observe_clock)),
                    patch.object(c.client, "listen_key", observe_key),
                ):
                    result = await asyncio.wait_for(
                        module.run(
                            c,
                            uuid4(),
                            connector=connector,
                            observation_seconds=duration,
                            progress_seconds=0.25,
                        ),
                        timeout=30,
                    )
                result["synthetic_renewal_elapsed_ms"] = renewal_elapsed
                return result
            return await module.run(
                c, uuid4(), connector=connector, observation_seconds=duration, progress_seconds=0.25
            )
        finally:
            c.client.sync_clock = original_clock


def test_original_25_minute_renewal_path_one_session_and_final_close(case):  # noqa: F811
    store, c, fake = case
    before = store.read("DEMO-test")
    report = asyncio.run(exercise(case, duration=5, accelerate=True))
    assert report["private_stability"] == "EMPTY_PRIVATE_SESSION_STABILITY_RENEWAL_VERIFIED", (
        json.dumps(report)
    )
    assert report["successful_key_renewals"] >= 1
    assert (
        report["synthetic_renewal_elapsed_ms"]
        and min(report["synthetic_renewal_elapsed_ms"]) >= 25 * 60 * 1000
    )
    assert report["key_create_success_count"] == report["key_close_success_count"] == 1
    assert report["observed_elapsed_ms"] >= 5000
    assert report["failure"] == report["completion_failure"] == "NONE"
    assert report["history_and_baselines_preserved"]
    assert (
        report["final_paused"]
        and not report["final_stream_ready"]
        and not report["final_reconciled"]
    )
    assert store.read("DEMO-test").orders == before.orders
    assert all(path == "/fapi/v1/listenKey" for m, path, _ in fake.calls if m != "GET")
    assert any(m == "PUT" and p == "/fapi/v1/listenKey" for m, p, _ in fake.calls)


def test_no_renewal_cannot_claim_stability_acceptance(case):  # noqa: F811
    _, _, fake = case
    report = asyncio.run(exercise(case))
    assert report["private_stability"] == "NOT_ACCEPTED"
    assert report["failure"] == "KEY_RENEWAL_NOT_VERIFIED"
    assert report["key_create_success_count"] == report["key_close_success_count"] == 1
    assert report["successful_key_renewals"] == 0 and report["new_entry_post_count"] == 0
    assert all(p == "/fapi/v1/listenKey" for m, p, _ in fake.calls if m != "GET")


def test_invalid_event_is_not_hidden_by_timer_or_cleanup(case):  # noqa: F811
    report = asyncio.run(exercise(case, bad_frame=True))
    assert report["private_stability"] == "NOT_ACCEPTED" and report["failure"] != "NONE"
    assert "PRIVATE_UNKNOWN_PAYLOAD" not in json.dumps(report)
    assert report["private_session_diagnostic"]["code"] == "EVENT_UNKNOWN"


def test_key_renewal_http_failure_is_not_counted_or_accepted(case, monkeypatch):  # noqa: F811
    _, c, fake = case
    original = c.client.send

    def reject(method, path, query, key):
        if method == "PUT" and path == "/fapi/v1/listenKey":
            return fake.response({"code": -1000, "msg": "PRIVATE_DETAIL"}, 500)
        return original(method, path, query, key)

    monkeypatch.setattr(c.client, "send", reject)
    report = asyncio.run(exercise(case, duration=5, accelerate=True))
    assert report["private_stability"] == "NOT_ACCEPTED"
    assert report["successful_key_renewals"] == 0 and report["failure"] != "NONE"
    assert "PRIVATE_DETAIL" not in json.dumps(report)


def test_key_close_failure_prevents_acceptance_after_successful_renewal(case, monkeypatch):  # noqa: F811
    _, c, fake = case
    original = c.client.send

    def reject(method, path, query, key):
        if method == "DELETE" and path == "/fapi/v1/listenKey":
            return fake.response({"code": -1000}, 500)
        return original(method, path, query, key)

    monkeypatch.setattr(c.client, "send", reject)
    report = asyncio.run(exercise(case, duration=5, accelerate=True))
    assert report["private_stability"] == "NOT_ACCEPTED"
    assert report["successful_key_renewals"] >= 1 and report["key_close_success_count"] == 0
    assert (
        report["synthetic_renewal_elapsed_ms"]
        and min(report["synthetic_renewal_elapsed_ms"]) >= 25 * 60 * 1000
    )
    assert report["failure"] == "KEY_CLOSE_NOT_VERIFIED"


def test_result_audit_failure_blocks_otherwise_verified_session(case, monkeypatch):  # noqa: F811
    store, _, _ = case
    original = store.change

    def fail(lease, marker, apply):
        if marker == "DEMO_PRIVATE_STABILITY_RESULT":
            raise OSError("PRIVATE_DATABASE_URL")
        return original(lease, marker, apply)

    monkeypatch.setattr(store, "change", fail)
    report = asyncio.run(exercise(case, duration=5, accelerate=True))
    assert report["private_stability"] == "NOT_ACCEPTED"
    assert report["failure"] == "RESULT_AUDIT_FAILED" and "PRIVATE_DATABASE_URL" not in json.dumps(
        report
    )


def test_basis_change_during_observation_is_rejected(case, monkeypatch):  # noqa: F811
    from decimal import Decimal

    store, c, _ = case
    original = module.progress

    def drift(stage, elapsed, client, target):
        if elapsed == 0:
            store.change(
                c.lease,
                "TEST_STABILITY_BASIS_DRIFT",
                lambda ledger: setattr(ledger, "daily_base", ledger.daily_base + Decimal(1)),
            )
        original(stage, elapsed, client, target)

    monkeypatch.setattr(module, "progress", drift)
    report = asyncio.run(exercise(case))
    assert report["failure"] == "HISTORY_CHANGED" and not report["history_and_baselines_preserved"]


def test_existing_operation_cannot_open_another_session(case):  # noqa: F811
    store, c, fake = case
    operation = uuid4()
    store.change(
        c.lease,
        "TEST_STABILITY_REPLAY",
        lambda ledger: ledger.operations.update(
            {str(operation): "EMPTY_PRIVATE_STABILITY_STARTED"}
        ),
    )
    calls = len(fake.calls)
    with pytest.raises(module.StabilityBlocked) as caught:
        asyncio.run(module.run(c, operation, observation_seconds=0.01))
    assert caught.value.code == "OPERATION_ALREADY_EXISTS" and len(fake.calls) == calls


def test_setup_timeout_is_reported_and_closes_key(case):  # noqa: F811
    _, c, _ = case

    @asynccontextmanager
    async def stalled(url):
        await asyncio.sleep(10)
        yield None

    report = asyncio.run(
        module.run(c, uuid4(), connector=stalled, ready_timeout=0.02, observation_seconds=0.01)
    )
    assert report["failure"] == "READY_TIMEOUT"
    assert report["key_create_success_count"] == report["key_close_success_count"] == 1
    assert report["final_paused"] and not report["final_stream_ready"]


def test_failed_result_without_ready_marker_cannot_be_replayed(case):  # noqa: F811
    from uuid import uuid5

    store, c, fake = case
    operation = uuid4()
    store.change(
        c.lease,
        "TEST_FAILED_STABILITY",
        lambda ledger: ledger.operations.update(
            {str(uuid5(operation, "stability-result")): "PRIVATE_STABILITY:READY_TIMEOUT"}
        ),
    )
    calls = len(fake.calls)
    with pytest.raises(module.StabilityBlocked) as caught:
        asyncio.run(module.run(c, operation))
    assert caught.value.code == "OPERATION_ALREADY_EXISTS" and len(fake.calls) == calls
