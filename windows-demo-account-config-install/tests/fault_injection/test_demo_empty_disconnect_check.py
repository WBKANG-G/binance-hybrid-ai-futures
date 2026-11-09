"""Real PostgreSQL + local WS: deliberate loss, durable gate, one reconnect."""

import asyncio
from contextlib import asynccontextmanager
from decimal import Decimal

import pytest
from sqlalchemy import select
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

import scripts.check_demo_disconnect as cli
from integrations.binance_usdm.demo import DemoFailure
from packages.domain.contracts import PositionState, RiskState
from packages.persistence.demo import events
from scripts.observe_demo_private import ObservationClient
from tests.integration.test_demo import enter
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_isolated import configure


@pytest.fixture
def empty_case(demo_system):  # noqa: F811
    system, _ = configure(demo_system)
    store, c, fake = system
    signal, _ = enter(system)
    c.cancel(str(signal.signal_id))

    def paused(ledger):
        # Seed the terminal closed state produced by completed validation,
        # retaining the real synthetic exchange cancellation and all requests.
        canceled = ledger.orders[str(signal.signal_id)]
        assert canceled.state.value == "CANCELED" and not canceled.filled
        canceled.position_state = PositionState.CLOSED
        ledger.paused = True
        ledger.requests["DS-old-unresolved"] = "UNKNOWN"
        ledger.requests["DX-old-unresolved"] = "SUBMITTING"

    store.change(c.lease, "TEST_EMPTY_PAUSED_HISTORY", paused)
    c.client = ObservationClient(
        c.client.settings, c.client.credentials, send=c.client.send, clock_ms=c.client.clock_ms
    )
    c.client.sync_clock()
    return store, c, fake


async def run_check(c, behavior="normal", **kwargs):
    connections = []

    async def handler(socket):
        await socket.wait_closed()

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]

        @asynccontextmanager
        async def connector(url):
            connections.append(len(connections) + 1)
            if behavior == "reconnect_fails" and len(connections) == 2:
                raise RuntimeError("PRIVATE_KEY_URL_MUST_NOT_LEAK")
            async with connect(f"ws://127.0.0.1:{port}") as socket:
                if behavior == "premature_close":
                    await socket.close()
                yield socket

        options = dict(connector=connector, drop_delay=0.01, observe_seconds=0.03, phase_timeout=3)
        options.update(kwargs)
        return await cli.check(c, **options), connections


def test_disconnect_marks_untrusted_then_one_reconnect_reconciles_and_preserves_history(empty_case):
    store, c, fake = empty_case
    before = store.read("DEMO-test")
    start = len(fake.calls)
    report, connections = asyncio.run(run_check(c))
    after = store.read("DEMO-test")
    assert report["disconnect_check"] == "EMPTY_SOCKET_LOSS_RECONNECT_VERIFIED"
    assert report["disconnect_code"] == "SOCKET_CLOSED"
    assert report["entry_gate_blocked_after_disconnect"] and report["reconnect_reconciled"]
    assert connections == [1, 2]
    assert not c.session_failed and after.paused and not after.stream_ready and not after.reconciled
    assert after.orders == before.orders and after.requests == before.requests
    assert after.daily_base == before.daily_base and after.weekly_base == before.weekly_base
    assert after.high_water == before.high_water and after.risk_state == RiskState.NORMAL
    assert after.last_private_session_failure.code == "SOCKET_CLOSED"
    writes = [(m, p) for m, p, _ in fake.calls[start:] if m != "GET"]
    assert writes == [("POST", "/fapi/v1/listenKey"), ("DELETE", "/fapi/v1/listenKey")] * 2
    with store.engine.connect() as conn:
        rows = conn.execute(
            select(events.c.kind, events.c.payload).where(events.c.account_id == "DEMO-test")
        ).all()
    kinds = [kind for kind, _ in rows]
    assert kinds.index("DEMO_EMPTY_WS_FAULT_BLOCKED") < kinds.index(
        "DEMO_EMPTY_WS_FAULT_RECONNECTED"
    )
    assert kinds[-1] == "DEMO_EMPTY_WS_FAULT_COMPLETE"
    assert all("PRIVATE_KEY_URL" not in payload for _, payload in rows)


@pytest.mark.parametrize("kind", ["local_position", "unpaused", "emergency", "pending_order"])
def test_occupied_or_unapproved_context_cannot_open_socket(empty_case, kind):
    store, c, fake = empty_case

    def change(ledger):
        if kind == "local_position":
            ledger.positions["BTCUSDT"] = Decimal("0.1")
        if kind == "unpaused":
            ledger.paused = False
        if kind == "emergency":
            ledger.risk_state = RiskState.EMERGENCY
        if kind == "pending_order":
            next(iter(ledger.orders.values())).open_quantity = Decimal("0.001")

    store.change(c.lease, "TEST_BLOCK_FAULT", change)
    start = len(fake.calls)
    with pytest.raises(cli.CheckBlocked, match="PAUSED_EMPTY_NORMAL_REQUIRED"):
        asyncio.run(run_check(c))
    assert len(fake.calls) == start


def test_remote_unowned_order_is_not_canceled_and_prevents_injection(empty_case):
    store, c, fake = empty_case
    raw = next(iter(fake.orders.values()))
    fake.orders["foreign"] = dict(
        raw, clientOrderId="foreign", orderId="9999", status="NEW", executedQty="0"
    )
    start = len(fake.calls)
    with pytest.raises(cli.CheckBlocked, match="EXCHANGE_NOT_EMPTY"):
        asyncio.run(run_check(c))
    assert all(m == "GET" for m, _, _ in fake.calls[start:])
    assert fake.orders["foreign"]["status"] == "NEW"


@pytest.mark.parametrize("behavior", ["premature_close", "reconnect_fails"])
def test_unplanned_failure_is_never_reported_as_expected_pass(empty_case, behavior, capsys):
    store, c, _ = empty_case
    with pytest.raises(DemoFailure):
        asyncio.run(run_check(c, behavior))
    assert "PRIVATE_KEY_URL" not in capsys.readouterr().out
    with store.engine.connect() as conn:
        assert not conn.execute(
            select(events.c.event_id).where(events.c.kind == "DEMO_EMPTY_WS_FAULT_COMPLETE")
        ).all()
    assert store.read("DEMO-test").paused


def test_gate_probe_uses_copy_and_checks_disconnect_independently_of_paused(empty_case):
    store, c, _ = empty_case
    before = store.read("DEMO-test")
    assert not cli.gate_blocked(c, before)  # Underlying ready stream, paused copy removed.
    c.disconnect()
    after = store.read("DEMO-test")
    assert cli.gate_blocked(c, after)
    assert before.paused and after.paused
    assert store.read("DEMO-test") == after


def test_changed_history_during_reconnect_blocks_completion(empty_case, monkeypatch):
    store, c, _ = empty_case
    original = c.connected
    count = 0

    def connected():
        nonlocal count
        original()
        count += 1
        if count == 2:
            store.change(
                c.lease,
                "TEST_FOREIGN_HISTORY_CHANGE",
                lambda ledger: ledger.requests.update({"DS-old-unresolved": "SUBMITTING"}),
            )

    monkeypatch.setattr(c, "connected", connected)
    with pytest.raises(DemoFailure):
        asyncio.run(run_check(c))
    with store.engine.connect() as conn:
        assert not conn.execute(
            select(events.c.event_id).where(events.c.kind == "DEMO_EMPTY_WS_FAULT_COMPLETE")
        ).all()
    assert store.read("DEMO-test").paused


@pytest.mark.parametrize("release_fails", [False, True])
def test_cli_runs_real_checked_flow_and_refuses_success_on_release_failure(
    empty_case, monkeypatch, capsys, release_fails
):
    import json

    store, c, fake = empty_case
    source = c.client
    store.release(c.lease)
    monkeypatch.setattr(cli, "ACCOUNT", "DEMO-test")
    monkeypatch.setattr("sys.argv", ["check_demo_disconnect", "--enable-fault-injection"])
    monkeypatch.setattr(cli, "credentials", lambda **kw: source.credentials)
    monkeypatch.setattr(cli, "create_engine", lambda *a, **kw: store.engine)
    monkeypatch.setattr(cli, "DemoStore", lambda engine: store)

    class BoundClient(ObservationClient):
        def __init__(self, cfg, key):
            super().__init__(source.settings, key, send=source.send, clock_ms=source.clock_ms)

    monkeypatch.setattr(cli, "ObservationClient", BoundClient)
    original_check = cli.check
    start = len(fake.calls)

    async def run():
        async def handler(socket):
            await socket.wait_closed()

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]

            @asynccontextmanager
            async def connector(url):
                async with connect(f"ws://127.0.0.1:{port}") as socket:
                    yield socket

            async def local_check(coordinator):
                return await original_check(
                    coordinator,
                    connector=connector,
                    drop_delay=0.01,
                    observe_seconds=0.03,
                    phase_timeout=3,
                )

            monkeypatch.setattr(cli, "check", local_check)
            if release_fails:

                def fail_release(lease):
                    raise RuntimeError("PRIVATE_KEY_URL_RELEASE")

                monkeypatch.setattr(store, "release", fail_release)
                with pytest.raises(SystemExit) as caught:
                    await asyncio.to_thread(cli.main)
                assert caught.value.code == 2
            else:
                await asyncio.to_thread(cli.main)

    asyncio.run(run())
    output = capsys.readouterr().out
    report = json.loads(output.splitlines()[-1])
    assert "PRIVATE_KEY_URL" not in output
    if release_fails:
        assert report["disconnect_check"] == "BLOCKED" and report["code"] == "LEASE_RELEASE_FAILED"
    else:
        assert report["disconnect_check"] == "EMPTY_SOCKET_LOSS_RECONNECT_VERIFIED"
    assert store.read("DEMO-test").paused
    assert all(m == "GET" or p == "/fapi/v1/listenKey" for m, p, _ in fake.calls[start:])


def test_timeout_before_deliberate_close_is_not_a_verified_fault(empty_case):
    store, c, _ = empty_case
    with pytest.raises((TimeoutError, cli.CheckBlocked)):
        asyncio.run(run_check(c, drop_delay=2, phase_timeout=0.1))
    assert store.read("DEMO-test").paused
    with store.engine.connect() as conn:
        assert not conn.execute(
            select(events.c.event_id).where(events.c.kind == "DEMO_EMPTY_WS_FAULT_COMPLETE")
        ).all()


def test_reconnect_observation_timeout_cannot_report_success(empty_case):
    store, c, _ = empty_case
    with pytest.raises(cli.CheckBlocked, match="RECONNECT_NOT_VERIFIED"):
        asyncio.run(run_check(c, observe_seconds=2, phase_timeout=0.5))
    final = store.read("DEMO-test")
    assert final.paused and not final.stream_ready and not final.reconciled
    with store.engine.connect() as conn:
        assert conn.execute(
            select(events.c.event_id).where(events.c.kind == "DEMO_EMPTY_WS_FAULT_BLOCKED")
        ).all()
        assert not conn.execute(
            select(events.c.event_id).where(events.c.kind == "DEMO_EMPTY_WS_FAULT_COMPLETE")
        ).all()
