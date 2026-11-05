"""No secrets retained; actual EventInbox validates replay with recorded clock evidence."""

import asyncio
import importlib.util
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from integrations.binance_usdm.demo import DemoClockSample, DemoFailure
from scripts.observe_demo_private import ObservationClient
from tests.integration.test_demo import system  # noqa: F401

spec = importlib.util.spec_from_file_location(
    "hint_capture", Path(__file__).with_name("capture_demo_wss_hints.py")
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def client(at=1000):
    return SimpleNamespace(
        clock_ms=lambda: at,
        clock_sample=DemoClockSample(before_ms=990, after_ms=1000, server_ms=995),
    )


def test_only_allowlisted_fields_retained_and_original_timestamps_replayed():
    capture = m.Capture()
    for kind in ["ACCOUNT_UPDATE", "ORDER_TRADE_UPDATE", "ALGO_UPDATE"]:
        capture.receive(
            json.dumps(
                {
                    "e": kind,
                    "E": 995,
                    "apiKey": "DO_NOT_RETAIN",
                    "listenKey": "PRIVATE",
                    "o": {"account": "PRIVATE"},
                }
            ),
            client(),
        )
    serialized = json.dumps(capture.hints)
    assert "DO_NOT_RETAIN" not in serialized and "PRIVATE" not in serialized
    assert all(set(r) == {"e", "E", "received_local_ms", "clock_sample"} for r in capture.hints)
    report = asyncio.run(m.replay(capture.hints))
    assert report["hint_replay"] == "CAPTURED_HINT_ADMISSION_REPLAY_PASSED"
    assert report["received"] == 3 and report["coalesced"] == 2
    assert (
        report["timestamps_rewritten"] is False and report["full_private_frames_replayed"] is False
    )


@pytest.mark.parametrize(
    "raw",
    [
        "[]",
        "NaN",
        '{"e":"PRIVATE","E":995}',
        '{"e":"listenKeyExpired","E":995}',
        '{"e":"ACCOUNT_UPDATE","E":true}',
    ],
)
def test_invalid_frames_rejected_without_capture(raw):
    capture = m.Capture()
    with pytest.raises((DemoFailure, ValueError)):
        capture.receive(raw, client())
    assert capture.hints == []


def test_empty_capture_does_not_pass_replay():
    assert asyncio.run(m.replay([]))["hint_replay"] == "NO_EVENTS_NOT_ACCEPTED"


@pytest.mark.parametrize("stamp", [-30001, 50000])
def test_replay_rejects_original_stale_or_future_timestamp(stamp):
    capture = m.Capture()
    capture.receive(json.dumps({"e": "ACCOUNT_UPDATE", "E": stamp}), client())
    with pytest.raises(DemoFailure):
        asyncio.run(m.replay(capture.hints))


def test_capture_size_and_count_bounds():
    capture = m.Capture()
    with pytest.raises(DemoFailure):
        capture.receive("x" * 1048577, client())
    for _ in range(m.MAX_HINTS):
        capture.receive('{"e":"ACCOUNT_UPDATE","E":995}', client())
    with pytest.raises(DemoFailure):
        capture.receive('{"e":"ACCOUNT_UPDATE","E":995}', client())
    assert len(capture.hints) == 128


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/fapi/v1/order"),
        ("DELETE", "/fapi/v1/order"),
        ("POST", "/fapi/v1/algoOrder"),
        ("DELETE", "/fapi/v1/algoOrder"),
        ("POST", "/fapi/v1/leverage"),
    ],
)
def test_existing_observation_client_blocks_order_and_account_mutation_before_send(method, path):
    c = object.__new__(ObservationClient)
    with pytest.raises(DemoFailure, match="forbids"):
        c.request(method, path, {})


def test_supervisor_captures_and_replays_actual_session_on_synthetic_pg_socket(system):  # noqa: F811
    store, c, fake = system
    store.change(c.lease, "TEST_PAUSE", lambda ledger: setattr(ledger, "paused", True))
    raw = json.dumps(
        {"e": "ACCOUNT_UPDATE", "E": int(fake.at.timestamp() * 1000), "private": "NEVER_PRINT"}
    )

    class Socket:
        def __init__(self):
            self.sent = False

        async def recv(self):
            if self.sent:
                await asyncio.Future()
            self.sent = True
            return raw

        async def ping(self):
            f = asyncio.get_running_loop().create_future()
            f.set_result(None)
            return f

    @asynccontextmanager
    async def connector(url):
        yield Socket()

    capture = m.Capture()
    before = store.read("DEMO-test")
    asyncio.run(capture.supervise(c, connector=connector, max_messages=1))
    assert not c.session_failed and len(capture.hints) == 1
    assert "NEVER_PRINT" not in json.dumps(capture.hints)
    assert (
        asyncio.run(m.replay(capture.hints))["hint_replay"]
        == "CAPTURED_HINT_ADMISSION_REPLAY_PASSED"
    )
    assert not [x for x in fake.calls if x[0] != "GET" and x[1] != "/fapi/v1/listenKey"]
    after = store.read("DEMO-test")
    assert after.orders == before.orders and after.requests == before.requests
    assert (
        after.daily_base == before.daily_base
        and after.weekly_base == before.weekly_base
        and after.high_water == before.high_water
    )


@pytest.mark.parametrize(
    "change",
    [
        {"paused": False},
        {"risk_state": "EMERGENCY"},
        {"period_keys_match": False},
        {"exchange_empty_verified": False},
        {"orders_enabled": True},
    ],
)
def test_readiness_gate_never_silently_accepts_changed_context(monkeypatch, change):
    report = dict(
        session_ready="READ_ONLY_READY_ENTRIES_STILL_PAUSED",
        risk_state="NORMAL",
        paused=True,
        period_keys_match=True,
        exchange_empty_verified=True,
        database_changed=False,
        orders_enabled=False,
        live_trading_enabled=False,
    )
    report.update(change)
    monkeypatch.setattr(
        m.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(returncode=0, stdout=json.dumps(report)),
    )
    with pytest.raises(ValueError):
        m.ready_check()


def test_main_blocks_before_observation_and_redacts_private_failure(monkeypatch, capsys):
    monkeypatch.setattr(m, "check_context", lambda: None)

    def blocked():
        raise ValueError("PRIVATE_KEY_OR_FRAME")

    monkeypatch.setattr(m, "ready_check", blocked)
    with pytest.raises(SystemExit):
        m.main()
    report = capsys.readouterr().out
    assert "PRIVATE_KEY_OR_FRAME" not in report
    assert json.loads(report)["stage"] == "READ_ONLY_READY"


def test_financial_snapshot_is_readonly_and_detects_financial_changes(system):  # noqa: F811
    from decimal import Decimal
    from uuid import uuid4

    from sqlalchemy import event

    store, _, _ = system
    account = "DEMO-WINDOWS-ISOLATED-BTC-V1"
    store.create(account, store.read("DEMO-test"))
    checked = []

    def inspect(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().startswith("SELECT demo_accounts.state"):
            assert conn.exec_driver_sql("SHOW transaction_read_only").scalar_one() == "on"
            checked.append(True)

    event.listen(store.engine, "before_cursor_execute", inspect)
    before = m.financial_hash(store.engine)
    event.remove(store.engine, "before_cursor_execute", inspect)
    assert checked
    lease = store.acquire(account, uuid4())
    store.change(
        lease,
        "TEST_METADATA_ONLY",
        lambda ledger: setattr(ledger, "stream_ready", not ledger.stream_ready),
    )
    assert m.financial_hash(store.engine) == before
    store.change(
        lease, "TEST_FINANCIAL_CHANGE", lambda ledger: setattr(ledger, "high_water", Decimal("2"))
    )
    assert m.financial_hash(store.engine) != before
