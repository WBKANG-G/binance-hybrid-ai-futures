"""Real PG and local WSS, synthetic fills only; production quantity cap is unchanged."""

import asyncio
import json
from contextlib import asynccontextmanager
from decimal import Decimal
from urllib.parse import parse_qs
from uuid import uuid4

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from services.execution import demo_tp_trigger_check as module
from services.execution import demo_validation as validation
from services.execution.demo_stream import session
from tests.integration.test_demo import posts
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401


@pytest.fixture
def case(preview_case):  # noqa: F811
    store, old, fake, controls, market = preview_case
    market.update(bid="80000", ask="80000.1", mark="80000")
    controls["liquidation"] = "40000"
    options = {"fill": True, "trigger": "TP", "tamper": False, "cancel_query": False}
    send = old.client.send

    def transport(method, path, query, key):
        params = {k: v[0] for k, v in parse_qs(query.get_secret_value()).items()}
        if path == "/fapi/v1/klines":
            start = int(fake.at.timestamp() * 1000) // 60000 * 60000 - 60000
            return fake.response([[start, "80000", "80100", "79900", "80000", "1", start + 59999]])
        response = send(method, path, query, key)
        raw = json.loads(response.body)
        if path == "/fapi/v1/exchangeInfo":
            for row in raw["symbols"][0]["filters"]:
                if row["filterType"] == "LOT_SIZE":
                    row.update(minQty="0.0001", stepSize="0.0001")
                if row["filterType"] == "MIN_NOTIONAL":
                    row["notional"] = "50"
            response = fake.response(raw)
        if (
            method == "POST"
            and path == "/fapi/v1/order"
            and params.get("type") == "LIMIT"
            and options["fill"]
        ):
            fake.fill(params["newClientOrderId"], Decimal(params["quantity"]), Decimal("80000"))
        if (
            method == "GET"
            and path == "/fapi/v1/order"
            and params.get("orderId")
            and options["tamper"]
        ):
            raw["executedQty"] = "0.0001"
            response = fake.response(raw)
        if (
            method == "GET"
            and path == "/fapi/v1/algoOrder"
            and options["cancel_query"]
            and raw.get("algoStatus") == "CANCELED"
        ):
            response = fake.response({"code": -2013}, 400)
        return response

    client = module.TPTriggerClient(
        old.client.settings.model_copy(update={"demo_trading_enabled": True}),
        old.client.credentials,
        send=transport,
        clock_ms=old.client.clock_ms,
    )
    client.sync_clock()
    c = module.TPTriggerCoordinator(store, old.lease, client)
    c.refresh_metadata()
    c.reconcile()
    store.change(c.lease, "TEST_PAUSE", lambda ledger: setattr(ledger, "paused", True))
    return store, c, fake, options


async def exercise(case, monkeypatch, operation=None):
    store, c, fake, options = case
    operation = operation or uuid4()
    triggered = False

    async def handler(socket):
        nonlocal triggered
        try:
            while True:
                ledger = store.read("DEMO-test")
                order = ledger.orders.get(str(operation))
                if (
                    not triggered
                    and options["trigger"]
                    and order
                    and ledger.validation_evidence.get(str(operation), {}).get("target_verified")
                ):
                    await asyncio.sleep(0.05)
                    fake.trigger(order.tp_algo_id if options["trigger"] == "TP" else order.algo_id)
                    triggered = True
                await socket.send(json.dumps({"e": "ACCOUNT_UPDATE", "E": c.client.clock_ms()}))
                await asyncio.sleep(0.02)
        except Exception:
            pass

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]

        @asynccontextmanager
        async def connector(url):
            async with connect(f"ws://127.0.0.1:{port}") as socket:
                yield socket

        async def local_session(coordinator, *, on_ready):
            await session(coordinator, connector=connector, on_ready=on_ready)

        monkeypatch.setattr(validation, "session", local_session)
        monkeypatch.setattr(module, "OBSERVATION_SECONDS", 0.8)
        return await module.run(c, operation)


def test_actual_shaped_tp_fill_precedes_original_sl_cleanup_with_no_operator_exit(
    case, monkeypatch
):
    store, c, fake, _ = case
    before = store.read("DEMO-test")
    report = asyncio.run(exercise(case, monkeypatch))
    assert report["tp_trigger_check"] == "TP_FILLED_THEN_REMAINING_SL_CANCELED_FLAT_VERIFIED", (
        report
    )
    assert report["failure"] == "NONE" and report["flat_confirmation"] == "VERIFIED_FLAT"
    assert report["signed_tp_fill_before_sl_delete_verified"] and report["sl_cancel_query_verified"]
    assert report["operator_exit_post_attempts"] == 0 and report["sl_delete_attempts"] == 1
    assert report["entry_post_attempts"] == report["new_entry_post_count"] == 1
    assert report["history_and_baselines_preserved"] and report["risk_state"] == "NORMAL"
    assert report["final_paused"] and len(posts(fake)) == 1
    order = next(iter(store.read("DEMO-test").orders.values()))
    assert order.quantity == Decimal("0.0007")
    assert order.signal.target_price == Decimal("80800")
    assert order.signal.initial_stop_price == Decimal("79600")
    assert module.history_preserved(before, store.read("DEMO-test"), str(order.signal.signal_id))


@pytest.mark.parametrize("mode", ["unfilled", "no_tp", "sl", "bad_qty", "missing_cancel"])
def test_other_outcomes_never_count_as_tp_success_and_end_flat(case, monkeypatch, mode):
    store, c, fake, options = case
    if mode == "unfilled":
        options.update(fill=False, trigger=False)
    elif mode == "no_tp":
        options["trigger"] = False
    elif mode == "sl":
        options["trigger"] = "SL"
    elif mode == "bad_qty":
        options["tamper"] = True
    else:
        options["cancel_query"] = True
    report = asyncio.run(exercise(case, monkeypatch))
    assert report["tp_trigger_check"] == "NOT_ACCEPTED", report
    assert not report["sl_cancel_query_verified"]
    assert not any(fake.positions.values())
    assert len([p for p in posts(fake) if p["type"] == "LIMIT"]) == 1
    if mode == "no_tp":
        assert report["operator_exit_post_attempts"] == 1
        assert report["risk_state"] == "EMERGENCY"
    assert store.read("DEMO-test").paused


def test_same_operation_cannot_start_another_session_or_entry(case, monkeypatch):
    _, c, fake, _ = case
    operation = uuid4()
    asyncio.run(exercise(case, monkeypatch, operation))
    before = len(fake.calls)
    with pytest.raises(Exception, match="must not be submitted again"):
        asyncio.run(module.run(c, operation))
    assert len(fake.calls) == before and len(posts(fake)) == 1


@pytest.mark.parametrize(
    "cap,style,direction",
    [
        ("0.0008", "PASSIVE", "LONG"),
        (None, "PASSIVE", "LONG"),
        ("0.0007", "OPPOSITE_QUOTE", "LONG"),
        ("0.0007", "PASSIVE", "SHORT"),
    ],
)
def test_wrong_scope_never_reserves_or_sends(case, cap, style, direction):
    store, c, fake, _ = case
    from services.risk.paper import RiskRejected
    from tests.integration.test_paper import signal, snapshot

    sig = signal(
        environment="DEMO",
        purpose="DEMO_VALIDATION",
        strategy=None,
        direction=direction,
        initial_stop_price="110" if direction == "SHORT" else "90",
        target_price="90" if direction == "SHORT" else "120",
    )
    before = store.read("DEMO-test")
    start = len(fake.calls)
    with pytest.raises(RiskRejected):
        c.reserve(sig, snapshot(), quantity_cap=Decimal(cap) if cap else None, entry_style=style)
    assert store.read("DEMO-test") == before and len(fake.calls) == start


def test_fresh_nonzero_position_blocks_stop_delete_even_after_tp_finished(case):
    store, c, fake, _ = case
    operation = uuid4()
    c.connected()
    c.resume_entries(uuid4())
    sig, gate, cap = validation.prepare(c, operation, "LONG", entry_style="PASSIVE")
    order = c.reserve(sig, gate, quantity_cap=cap)
    c.submit(str(operation))
    c.reconcile()
    order = store.read("DEMO-test").orders[str(operation)]
    fake.trigger(order.tp_algo_id)
    fake.positions["BTCUSDT"] = order.filled
    fake.prices["BTCUSDT"] = order.average_price
    start = len(fake.calls)
    with pytest.raises(module.NonFlatObserved):
        c.client.request("DELETE", "/fapi/v1/algoOrder", {"clientAlgoId": order.algo_id})
    assert fake.algos[order.algo_id]["algoStatus"] == "NEW"
    assert not any(method == "DELETE" for method, _, _ in fake.calls[start:])
