"""One operational minimum-size DEMO validation; real PG/localhost WS, fake exchange."""

import asyncio
from contextlib import asynccontextmanager
from decimal import Decimal as D
from urllib.parse import parse_qs
from uuid import uuid4

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

import services.execution.demo_validation as module
from integrations.binance_usdm.demo import DemoFailure, DemoSettings
from services.execution.demo_stream import session
from tests.integration.test_demo import posts
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401


@pytest.fixture
def case(preview_case):  # noqa: F811
    store, c, fake, controls, market = preview_case
    c.client.settings = DemoSettings.model_validate(
        c.client.settings.model_dump() | {"demo_trading_enabled": True}
    )
    send = c.client.send
    options = {"fill": False, "stale_candle": False, "margin_controls": controls}

    def transport(method, path, query, key):
        if path == "/fapi/v1/klines":
            fake.calls.append((method, path, {}))
            start = int(fake.at.timestamp() * 1000) // 60000 * 60000 - 60000
            if options["stale_candle"]:
                start -= 180000
            return fake.response([[start, "100", "101", "99", "100", "1", start + 59999]])
        response = send(method, path, query, key)
        if method == "POST" and path == "/fapi/v1/order" and options["fill"]:
            params = {k: v[0] for k, v in parse_qs(query.get_secret_value()).items()}
            if params.get("type") == "LIMIT":
                fake.fill(
                    params["newClientOrderId"], options.get("fill_quantity", D(params["quantity"]))
                )
        return response

    c.client.send = transport
    return store, c, fake, options


async def exercise(c, operation, direction, monkeypatch, *, entry_style="PASSIVE"):
    async def handler(socket):
        await socket.wait_closed()

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]

        @asynccontextmanager
        async def connector(url):
            async with connect(f"ws://127.0.0.1:{port}") as socket:
                yield socket

        async def local_session(coordinator, *, on_ready):
            await session(coordinator, connector=connector, on_ready=on_ready)

        monkeypatch.setattr(module, "session", local_session)
        await module.validate_once(
            c, operation, direction, observation_seconds=0.03, entry_style=entry_style
        )


def test_unfilled_minimum_validation_cancels_and_verifies_flat(case, monkeypatch, capsys):
    store, c, fake, _ = case
    operation = uuid4()
    asyncio.run(exercise(c, operation, "LONG", monkeypatch))
    order = store.read("DEMO-test").orders[str(operation)]
    assert order.quantity == D("0.05") and order.remaining == 0 and order.filled == 0
    assert len(posts(fake)) == 1 and D(posts(fake)[0]["quantity"]) == order.quantity
    assert order.signal.purpose == "DEMO_VALIDATION" and order.signal.strategy is None
    assert "FINISHED_FLAT" in capsys.readouterr().out
    assert not store.read("DEMO-test").stream_ready


def test_filled_minimum_validation_protects_then_reduces_and_cleans_up(case, monkeypatch, capsys):
    store, c, fake, options = case
    options["fill"] = True
    operation = uuid4()
    asyncio.run(exercise(c, operation, "LONG", monkeypatch))
    ledger = store.read("DEMO-test")
    assert ledger.validation_evidence[str(operation)] == {
        "stop_verified": True,
        "target_verified": True,
    }
    assert fake.positions["BTCUSDT"] == 0
    assert all(p["type"] == "LIMIT" or p.get("reduceOnly") == "true" for p in posts(fake))
    assert all(a["algoStatus"] != "NEW" for a in fake.algos.values())
    output = capsys.readouterr().out
    assert "FINISHED_FLAT" in output and '"stop_verified": true' in output


def test_same_operation_recovers_without_new_entry(case, monkeypatch):
    store, c, fake, _ = case
    operation = uuid4()
    asyncio.run(exercise(c, operation, "LONG", monkeypatch))
    asyncio.run(exercise(c, operation, "LONG", monkeypatch))
    assert len(posts(fake)) == 1
    assert len(store.read("DEMO-test").orders) == 1


def test_stale_candle_never_creates_an_entry(case, monkeypatch):
    store, c, fake, options = case
    options["stale_candle"] = True
    with pytest.raises(DemoFailure):
        asyncio.run(exercise(c, uuid4(), "LONG", monkeypatch))
    assert not posts(fake) and not store.read("DEMO-test").orders


def test_lost_response_is_not_resubmitted_by_validation(case, monkeypatch):
    store, c, fake, _ = case
    fake.lost_submit = True
    asyncio.run(exercise(c, uuid4(), "LONG", monkeypatch))
    assert len(posts(fake)) == 1


def test_wrong_direction_for_existing_operation_is_rejected_before_session(case, monkeypatch):
    store, c, fake, _ = case
    operation = uuid4()
    asyncio.run(exercise(c, operation, "LONG", monkeypatch))
    before = len(fake.calls)
    with pytest.raises(DemoFailure, match="operation ID conflict"):
        asyncio.run(module.validate_once(c, operation, "SHORT", observation_seconds=0.03))
    assert len(fake.calls) == before


def test_stop_failure_never_reports_acceptance_even_after_flat(case, monkeypatch, capsys):
    store, c, fake, options = case
    options["fill"] = True
    fake.reject_stop = True
    with pytest.raises(DemoFailure):
        asyncio.run(exercise(c, uuid4(), "LONG", monkeypatch))
    assert fake.positions["BTCUSDT"] == 0
    assert '"filled_protection_and_flat_verified": false' in capsys.readouterr().out


def test_unknown_exit_remains_unresolved_and_is_not_resubmitted(case, monkeypatch, capsys):
    store, c, fake, options = case
    options["fill"] = True
    fake.defer_exit = True
    with pytest.raises(DemoFailure):
        asyncio.run(exercise(c, uuid4(), "LONG", monkeypatch))
    exits = [p for p in posts(fake) if p["type"] == "MARKET"]
    assert len(exits) == 1 and exits[0]["reduceOnly"] == "true"
    assert "UNRESOLVED_DO_NOT_REPEAT" in capsys.readouterr().out


def test_partial_validation_cancels_residual_then_closes_actual_quantity(case, monkeypatch, capsys):
    store, c, fake, options = case
    options["fill"] = True
    options["fill_quantity"] = D("0.02")
    operation = uuid4()
    asyncio.run(exercise(c, operation, "LONG", monkeypatch))
    order = store.read("DEMO-test").orders[str(operation)]
    assert order.filled == D("0.02") and order.remaining == 0 and order.open_quantity == 0
    exits = [p for p in posts(fake) if p["type"] == "MARKET"]
    assert len(exits) == 1 and D(exits[0]["quantity"]) == D("0.02")
    assert '"entry_outcome": "PARTIAL_FILL"' in capsys.readouterr().out


def test_short_validation_uses_opposite_protection_and_reduce_exit(case, monkeypatch):
    store, c, fake, options = case
    options["fill"] = True
    options["margin_controls"]["liquidation"] = "150"
    operation = uuid4()
    asyncio.run(exercise(c, operation, "SHORT", monkeypatch))
    entry = next(p for p in posts(fake) if p["type"] == "LIMIT")
    assert entry["side"] == "SELL"
    exits = [p for p in posts(fake) if p["type"] == "MARKET"]
    assert len(exits) == 1 and exits[0]["side"] == "BUY" and exits[0]["reduceOnly"] == "true"
    assert all(a["side"] == "BUY" for a in fake.algos.values())
    assert fake.positions["BTCUSDT"] == 0
