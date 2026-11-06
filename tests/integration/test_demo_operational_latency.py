"""History-heavy operational checks retain current truth and strict quote freshness."""

import asyncio
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from integrations.binance_usdm.demo import DemoFailure
from integrations.binance_usdm.demo_snapshot import snapshot as full_snapshot
from packages.domain.contracts import PositionState, RiskState
from services.execution.demo_validation import prepare
from tests.integration.test_demo import posts
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_validation import case as validation_case  # noqa: F401
from tests.integration.test_demo_validation import exercise


@pytest.fixture
def historical_case(validation_case, monkeypatch):  # noqa: F811
    store, c, fake, options = validation_case
    options["fill"] = True
    operation = uuid4()
    asyncio.run(exercise(c, operation, "LONG", monkeypatch))
    base = store.read("DEMO-test").orders[str(operation)].model_copy(deep=True)
    assert base.position_state == PositionState.CLOSED and base.exit_state == "FILLED"

    def seed(ledger):
        for i in range(3):
            order = base.model_copy(deep=True)
            signal_id = uuid4()
            order.signal = order.signal.model_copy(update={"signal_id": signal_id})
            suffix = uuid4().hex[:30]
            order.client_id, order.exit_id = "DE-" + suffix, "DX-" + suffix
            order.algo_id, order.tp_algo_id = "DS-" + suffix, "DT-" + suffix
            order.server_order_id = str(900 + i * 2)
            for old_id, new_id, server in (
                (base.client_id, order.client_id, order.server_order_id),
                (base.exit_id, order.exit_id, str(901 + i * 2)),
            ):
                fake.orders[new_id] = dict(
                    fake.orders[old_id], clientOrderId=new_id, orderId=server
                )
            for old_id, new_id in (
                (base.algo_id, order.algo_id),
                (base.tp_algo_id, order.tp_algo_id),
            ):
                fake.algos[new_id] = dict(
                    fake.algos[old_id], clientAlgoId=new_id, algoId=str(2000 + len(fake.algos))
                )
            ledger.orders[str(signal_id)] = order
        ledger.risk_state = RiskState.NORMAL
        ledger.paused = True

    store.change(c.lease, "TEST_VERIFIED_CLOSED_HISTORY", seed)
    c.connected()
    return store, c, fake, options


def latency(c, fake, milliseconds=350):
    original = c.client.send

    def send(method, path, query, key):
        response = original(method, path, query, key)
        if method == "GET":
            fake.at += timedelta(milliseconds=milliseconds)
        return response

    c.client.send = send


def test_history_latency_does_not_expire_fresh_preview(historical_case):
    store, c, fake, _ = historical_case
    latency(c, fake)
    start = len(fake.calls)
    signal, gate, cap = prepare(c, uuid4(), "LONG")
    assert cap > 0 and signal.suggested_limit_price > 0
    assert fake.at - gate.market_at <= timedelta(seconds=5)
    assert all(method == "GET" for method, _, _ in fake.calls[start:])
    assert not any(
        path in {"/fapi/v1/order", "/fapi/v1/algoOrder"} for _, path, _ in fake.calls[start:]
    )


def test_full_history_review_still_queries_every_closed_original_id(historical_case):
    store, c, fake, _ = historical_case
    ledger = store.read("DEMO-test")
    start = len(fake.calls)
    remote = full_snapshot(c.client, ledger, store.now())
    for order in ledger.orders.values():
        assert order.client_id in remote.orders and order.exit_id in remote.orders
        assert order.algo_id in remote.algos and order.tp_algo_id in remote.algos
    assert sum(path == "/fapi/v1/order" for _, path, _ in fake.calls[start:]) == 8


def test_current_open_lists_and_unknown_intents_are_never_skipped(historical_case):
    from integrations.binance_usdm.demo_snapshot import operational_snapshot

    store, c, fake, _ = historical_case
    ledger = store.read("DEMO-test")
    order = next(iter(ledger.orders.values()))
    fake.orders[order.exit_id]["status"] = "NEW"
    fake.algos[order.algo_id]["algoStatus"] = "NEW"
    remote = operational_snapshot(c.client, ledger, store.now())
    assert remote.orders[order.exit_id]["status"] == "NEW"
    assert remote.algos[order.algo_id]["algoStatus"] == "NEW"
    assert order.client_id in remote.orders
    fake.orders[order.exit_id]["status"] = "FILLED"
    fake.algos[order.algo_id]["algoStatus"] = "CANCELED"
    order.exit_state = "UNKNOWN"
    remote = operational_snapshot(c.client, ledger, store.now())
    assert order.exit_id in remote.orders and order.client_id in remote.orders


@pytest.mark.parametrize(
    "field,value",
    [
        ("position_state", PositionState.EXIT_PENDING),
        ("open_quantity", Decimal("0.001")),
        ("remaining", Decimal("0.001")),
        ("actual_risk", Decimal(1)),
        ("protected", True),
        ("exit_state", "UNKNOWN"),
        ("state", "UNKNOWN"),
    ],
)
def test_incomplete_local_proof_cannot_skip_original_id_queries(historical_case, field, value):
    from integrations.binance_usdm.demo_snapshot import operational_snapshot
    from packages.domain.contracts import OrderState

    store, c, fake, _ = historical_case
    ledger = store.read("DEMO-test")
    order = next(iter(ledger.orders.values()))
    if field == "state":
        value = OrderState.UNKNOWN
    setattr(order, field, value)
    remote = operational_snapshot(c.client, ledger, store.now())
    assert order.client_id in remote.orders and order.exit_id in remote.orders


def test_stale_signed_clock_is_rechecked_and_guard_itself_is_unchanged(historical_case):
    store, c, fake, _ = historical_case
    c.client.synced_at -= 61000
    count = len(fake.calls)
    with pytest.raises(DemoFailure, match="Clock stale"):
        c.client.request("GET", "/fapi/v3/account")
    assert len(fake.calls) == count
    c.reconcile()
    assert store.read("DEMO-test").reconciled
    assert fake.calls[count] == ("GET", "/fapi/v1/time", {})


def test_zero_fill_canceled_order_with_unknown_exit_is_not_hidden(historical_case):
    from integrations.binance_usdm.demo_snapshot import operational_snapshot
    from packages.domain.contracts import OrderState

    store, c, fake, _ = historical_case
    ledger = store.read("DEMO-test")
    order = next(iter(ledger.orders.values()))
    order.state, order.filled, order.exit_state = OrderState.CANCELED, Decimal(0), "UNKNOWN"
    remote = operational_snapshot(c.client, ledger, store.now())
    assert order.client_id in remote.orders and order.exit_id in remote.orders


def test_unattributed_remote_position_is_still_detected(historical_case):
    store, c, fake, _ = historical_case
    fake.positions["BTCUSDT"], fake.prices["BTCUSDT"] = Decimal("0.001"), Decimal("100")
    c.reconcile()
    ledger = store.read("DEMO-test")
    assert ledger.positions["BTCUSDT"] != 0
    assert not ledger.reconciled or ledger.risk_state != RiskState.NORMAL


def test_known_leftover_protection_is_removed_only_after_flat_proof(historical_case):
    store, c, fake, _ = historical_case
    order = next(iter(store.read("DEMO-test").orders.values()))
    fake.algos[order.algo_id]["algoStatus"] = "NEW"
    c.reconcile()
    assert fake.algos[order.algo_id]["algoStatus"] == "CANCELED"
    assert fake.positions["BTCUSDT"] == 0


def test_filled_validation_with_latency_and_history_keeps_single_entry_and_exit(
    historical_case, monkeypatch, capsys
):
    store, c, fake, _ = historical_case
    latency(c, fake, milliseconds=250)
    start = len(posts(fake))
    asyncio.run(exercise(c, uuid4(), "LONG", monkeypatch))
    submitted = posts(fake)[start:]
    assert len(submitted) == 2
    assert submitted[0]["type"] == "LIMIT"
    assert submitted[1]["type"] == "MARKET" and submitted[1]["reduceOnly"] == "true"
    assert fake.positions["BTCUSDT"] == 0
    assert '"filled_protection_and_flat_verified": true' in capsys.readouterr().out


def test_secret_exception_text_is_not_exposed():
    from services.execution.demo_validation import safe_failure

    assert (
        safe_failure(DemoFailure("SECRET-URL?signature=PRIVATE")) == "DEMO_SESSION_OR_QUERY_FAILED"
    )
    assert safe_failure(DemoFailure("Clock stale or cooldown active")) == "CLOCK_STALE_OR_COOLDOWN"


def test_entry_prepare_failure_reports_safe_stage_and_no_new_order(
    historical_case, monkeypatch, capsys
):
    import services.execution.demo_validation as module
    from services.execution.demo_preview import PreviewBlocked

    store, c, fake, _ = historical_case
    operation = uuid4()
    count = len(posts(fake))

    def fail(*args, **kwargs):
        raise PreviewBlocked("Preview quotes expired during final account verification")

    monkeypatch.setattr(module, "prepare", fail)
    with pytest.raises(DemoFailure):
        asyncio.run(exercise(c, operation, "LONG", monkeypatch))
    assert str(operation) not in store.read("DEMO-test").orders
    assert len(posts(fake)) == count
    output = capsys.readouterr().out
    assert '"validation_stage": "PREPARE_ENTRY"' in output
    assert '"session_failure": "QUOTE_EXPIRED_DURING_ACCOUNT_CHECK"' in output
