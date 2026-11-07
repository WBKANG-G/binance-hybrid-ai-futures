"""A known single initial execution may settle once, without weakening ordinary pairing."""

import asyncio
import json
from decimal import Decimal
from urllib.parse import parse_qs
from uuid import uuid4

import pytest

from integrations.binance_usdm.demo import DemoFailure
from tests.integration.test_demo import posts
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_validation import case as validation_case  # noqa: F401
from tests.integration.test_demo_validation import exercise


@pytest.fixture(autouse=True)
def fast_confirmation(monkeypatch):
    import services.execution.demo_validation as module

    monkeypatch.setattr(module, "FLAT_CONFIRMATION_INTERVAL_SECONDS", 0)


@pytest.fixture
def fill_case(validation_case):  # noqa: F811
    store, c, fake, options = validation_case
    options["fill"] = True
    original = c.client.send
    state = {"entry": None, "reads": 0, "mode": "positions", "proof": None}

    def send(method, path, query, key):
        params = {k: v[0] for k, v in parse_qs(query.get_secret_value()).items()}
        if path == "/fapi/v1/userTrades":
            fake.calls.append((method, path, params))
            proof = dict(state["proof"])
            if state["mode"] == "stale_proof":
                proof["time"] -= 31000
            if state["mode"] == "wrong_proof":
                proof["orderId"] = "9999"
            if state["mode"] == "wrong_fee":
                proof["commission"] = "0.02"
            if state["mode"] == "realized_pnl":
                proof["realizedPnl"] = "1"
            if state["mode"] == "foreign_asset":
                proof["commissionAsset"] = "BTC"
            if state["mode"] == "multi_fill":
                return fake.response([proof, dict(proof, id=2)])
            if state["mode"] == "missing_proof":
                return fake.response([])
            return fake.response([proof])
        if (
            state["entry"]
            and path == "/fapi/v3/account"
            and state["mode"] == "unstable_third"
            and state["reads"] == 2
        ):
            fake.wallet -= Decimal(1)
        response = original(method, path, query, key)
        if method == "POST" and path == "/fapi/v1/order" and params["type"] == "LIMIT":
            state["entry"] = params["newClientOrderId"]
            raw = fake.orders[state["entry"]]
            fee = (
                Decimal(raw["executedQty"])
                * Decimal(raw["avgPrice"])
                * c.client.settings.policy.fee_rate
                / 2
            )
            state["proof"] = dict(
                symbol="BTCUSDT",
                orderId=raw["orderId"],
                id=1,
                time=int(fake.at.timestamp() * 1000),
                positionSide="BOTH",
                side=raw["side"],
                commissionAsset="USDT",
                commission=str(fee),
                realizedPnl="0",
                qty=raw["executedQty"],
                price=raw["avgPrice"],
            )
            fake.wallet -= fee
            if state["mode"] == "cash_mismatch":
                fake.wallet -= Decimal(1)
            if state["mode"] == "foreign_order":
                fake.orders["foreign-entry"] = dict(
                    raw,
                    clientOrderId="foreign-entry",
                    orderId="9999",
                    status="NEW",
                    executedQty="0",
                    avgPrice="0",
                )
            if state["mode"] == "foreign_algo":
                fake.algos["foreign-stop"] = dict(
                    symbol="BTCUSDT",
                    clientAlgoId="foreign-stop",
                    algoId="9999",
                    side="SELL",
                    orderType="STOP_MARKET",
                    algoStatus="NEW",
                    triggerPrice="99",
                    closePosition=True,
                    positionSide="BOTH",
                    workingType="MARK_PRICE",
                    priceProtect=False,
                    algoType="CONDITIONAL",
                )
            if state["mode"] == "transfer":
                fake.incomes.append(dict(incomeType="TRANSFER", asset="USDT", tranId=9, income="1"))
        if state["entry"] and path == "/fapi/v3/positionRisk":
            state["reads"] += 1
            if state["reads"] == 1 and state["mode"] != "wallet":
                data = json.loads(response.body)
                return fake.response([r for r in data if r["symbol"] != "BTCUSDT"])
            if state["mode"] == "leverage" and state["reads"] == 2:
                fake.leverage = "3"
        if (
            state["entry"]
            and path == "/fapi/v3/account"
            and state["mode"] == "wallet"
            and state["reads"] == 0
        ):
            data = json.loads(response.body)
            data["totalWalletBalance"] = str(fake.wallet + Decimal(state["proof"]["commission"]))
            return fake.response(data)
        return response

    c.client.send = send
    return store, c, fake, options, state


@pytest.mark.parametrize("mode", ["positions", "wallet"])
def test_initial_position_propagation_with_signed_fill_gets_sl_tp_and_finishes_flat(
    fill_case, monkeypatch, capsys, mode
):
    store, c, fake, options, state = fill_case
    state["mode"] = mode
    operation = uuid4()
    asyncio.run(exercise(c, operation, "LONG", monkeypatch))
    ledger = store.read("DEMO-test")
    assert str(operation) in ledger.initial_fill_settlements
    assert ledger.validation_evidence[str(operation)] == {
        "stop_verified": True,
        "target_verified": True,
    }
    assert ledger.orders[str(operation)].position_state.value == "CLOSED"
    assert ledger.reconciliation_settle_reason == "VERIFIED_INITIAL_FILL"
    assert (
        "positions" if mode == "positions" else "wallet_balance"
    ) in ledger.last_reconciliation_mismatch_fields
    assert len(posts(fake)) == 2 and len(posts(fake, "/fapi/v1/algoOrder")) == 2
    output = capsys.readouterr().out
    assert '"initial_fill_settled": true' in output
    assert '"filled_protection_and_flat_verified": true' in output


@pytest.mark.parametrize(
    "mode",
    [
        "cash_mismatch",
        "transfer",
        "leverage",
        "unstable_third",
        "wrong_proof",
        "wrong_fee",
        "realized_pnl",
        "foreign_asset",
        "multi_fill",
        "missing_proof",
        "stale_proof",
        "foreign_order",
        "foreign_algo",
    ],
)
def test_unexplained_changes_and_incomplete_proof_still_exit_without_acceptance(
    fill_case, monkeypatch, capsys, mode
):
    store, c, fake, options, state = fill_case
    state["mode"] = mode
    operation = uuid4()
    with pytest.raises(DemoFailure):
        asyncio.run(exercise(c, operation, "LONG", monkeypatch))
    ledger = store.read("DEMO-test")
    assert str(operation) not in ledger.initial_fill_settlements
    assert fake.positions["BTCUSDT"] == 0
    assert ledger.paused and ledger.risk_state.value == "EMERGENCY"
    assert len(posts(fake, "/fapi/v1/algoOrder")) == 0
    assert len(posts(fake)) == 2
    assert sum(path == "/fapi/v1/userTrades" for _, path, _ in fake.calls) <= 1
    assert ledger.last_reconciliation_mismatch_fields
    assert '"filled_protection_and_flat_verified": false' in capsys.readouterr().out


def test_partial_initial_execution_is_protected_then_residual_canceled_before_exit(
    fill_case, monkeypatch, capsys
):
    store, c, fake, options, state = fill_case
    options["fill_quantity"] = Decimal("0.02")
    operation = uuid4()
    asyncio.run(exercise(c, operation, "LONG", monkeypatch))
    order = store.read("DEMO-test").orders[str(operation)]
    assert order.filled == Decimal("0.02") and order.remaining == 0 and order.open_quantity == 0
    assert len(posts(fake, "/fapi/v1/algoOrder")) == 2
    assert '"filled_protection_and_flat_verified": true' in capsys.readouterr().out


def test_short_initial_fill_keeps_direction_and_single_reduce_exit(fill_case, monkeypatch, capsys):
    store, c, fake, options, state = fill_case
    options["margin_controls"]["liquidation"] = "150"
    operation = uuid4()
    asyncio.run(exercise(c, operation, "SHORT", monkeypatch, entry_style="OPPOSITE_QUOTE"))
    assert str(operation) in store.read("DEMO-test").initial_fill_settlements
    assert posts(fake)[0]["side"] == "SELL"
    assert posts(fake)[-1]["side"] == "BUY" and posts(fake)[-1]["reduceOnly"] == "true"
    assert '"filled_protection_and_flat_verified": true' in capsys.readouterr().out


def test_session_error_cannot_be_hidden_by_timeout_during_finally_cleanup(
    fill_case, monkeypatch, capsys
):
    import threading
    import time
    from contextlib import asynccontextmanager

    from websockets.asyncio.client import connect
    from websockets.asyncio.server import serve

    import services.execution.demo_stream as stream
    import services.execution.demo_validation as module

    store, c, fake, options, state = fill_case
    command = c.command
    delayed = threading.Event()

    def pause(operation, action):
        if action == "PAUSE_ENTRIES" and not delayed.is_set():
            delayed.set()
            time.sleep(0.15)
        return command(operation, action)

    monkeypatch.setattr(c, "command", pause)

    async def run():
        ready = asyncio.Event()
        loop = asyncio.get_running_loop()

        async def handler(socket):
            await ready.wait()
            await socket.send(
                json.dumps(
                    {"e": "UNRECOGNIZED_PRIVATE_EVENT", "E": int(fake.at.timestamp() * 1000)}
                )
            )
            await socket.wait_closed()

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]

            @asynccontextmanager
            async def connector(url):
                async with connect(f"ws://127.0.0.1:{port}") as socket:
                    yield socket

            async def failing_session(coordinator, *, on_ready):
                def callback():
                    on_ready()
                    loop.call_soon_threadsafe(ready.set)

                await stream.session(coordinator, connector=connector, on_ready=callback)

            monkeypatch.setattr(module, "session", failing_session)
            with pytest.raises(DemoFailure):
                await module.validate_once(c, uuid4(), "LONG", observation_seconds=0.05)

    asyncio.run(run())
    assert fake.positions["BTCUSDT"] == 0 and len(posts(fake)) == 2
    output = capsys.readouterr().out
    assert '"session_error": true' in output
    assert '"filled_protection_and_flat_verified": false' in output
