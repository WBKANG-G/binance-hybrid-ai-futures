"""Multiple same-order executions retain all ordinary initial-fill safeguards."""

import asyncio
import json
from decimal import Decimal
from uuid import uuid4

import pytest

from integrations.binance_usdm.demo import DemoFailure
from tests.integration.test_demo import posts
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_initial_fill_settlement import fill_case  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_validation import case as validation_case  # noqa: F401
from tests.integration.test_demo_validation import exercise


@pytest.fixture(autouse=True)
def fast_confirmation(monkeypatch):
    import services.execution.demo_validation as module

    monkeypatch.setattr(module, "FLAT_CONFIRMATION_INTERVAL_SECONDS", 0)


@pytest.fixture
def multi_case(fill_case):  # noqa: F811
    store, c, fake, options, state = fill_case
    options["fill_quantity"] = Decimal("0.02")
    source = c.client.send
    controls = {"fault": None}

    def send(method, path, query, key):
        response = source(method, path, query, key)
        if path != "/fapi/v1/userTrades":
            return response
        original = json.loads(response.body)[0]
        a = original | {
            "qty": str(Decimal(original["qty"]) / 2),
            "commission": str(Decimal(original["commission"]) / 2),
            "time": original["time"] - 2640,
        }
        b = a | {"id": 2, "time": original["time"]}
        if controls["fault"] == "duplicate":
            b["id"] = a["id"]
        if controls["fault"] == "wrong_order":
            b["orderId"] = "9999"
        if controls["fault"] == "wrong_fee":
            b["commission"] = "1"
        if controls["fault"] == "different_price":
            b["price"] = str(Decimal(b["price"]) + 1)
        if controls["fault"] == "incomplete":
            return fake.response([a])
        if controls["fault"] == "stale":
            a["time"] -= 31000
        return fake.response([b, a])

    c.client.send = send
    return fill_case, controls


@pytest.mark.parametrize(
    "mode,direction", [("positions", "LONG"), ("wallet", "LONG"), ("positions", "SHORT")]
)
def test_two_initial_partial_executions_get_protection_and_finish_flat(
    multi_case, monkeypatch, capsys, mode, direction
):
    (store, c, fake, options, state), _ = multi_case
    state["mode"] = mode
    if direction == "SHORT":
        options["margin_controls"]["liquidation"] = "150"
    operation = uuid4()
    asyncio.run(
        exercise(
            c,
            operation,
            direction,
            monkeypatch,
            entry_style="OPPOSITE_QUOTE" if direction == "SHORT" else "PASSIVE",
        )
    )
    ledger = store.read("DEMO-test")
    order = ledger.orders[str(operation)]
    assert str(operation) in ledger.initial_fill_settlements
    assert ledger.validation_evidence[str(operation)] == {
        "stop_verified": True,
        "target_verified": True,
    }
    assert order.filled == Decimal("0.02") and order.remaining == 0 and order.open_quantity == 0
    assert order.state.value == "CANCELED" and order.position_state.value == "CLOSED"
    assert len(posts(fake)) == 2 and len(posts(fake, "/fapi/v1/algoOrder")) == 2
    assert fake.positions["BTCUSDT"] == 0
    assert '"filled_protection_and_flat_verified": true' in capsys.readouterr().out


@pytest.mark.parametrize(
    "fault", ["duplicate", "wrong_order", "wrong_fee", "different_price", "incomplete", "stale"]
)
def test_incomplete_or_unverified_set_still_flattens_without_acceptance(
    multi_case, monkeypatch, capsys, fault
):
    (store, c, fake, _, _), controls = multi_case
    controls["fault"] = fault
    operation = uuid4()
    with pytest.raises(DemoFailure):
        asyncio.run(exercise(c, operation, "LONG", monkeypatch))
    ledger = store.read("DEMO-test")
    assert str(operation) not in ledger.initial_fill_settlements
    assert ledger.reconciliation_settle_reason == "EXECUTIONS_PROOF_MISMATCH"
    assert ledger.paused and ledger.risk_state.value == "EMERGENCY"
    assert fake.positions["BTCUSDT"] == 0 and len(posts(fake)) == 2
    assert not posts(fake, "/fapi/v1/algoOrder")
    assert '"filled_protection_and_flat_verified": false' in capsys.readouterr().out


def test_valid_multiple_executions_do_not_explain_unrelated_cash_change(multi_case, monkeypatch):
    (store, c, fake, _, state), _ = multi_case
    state["mode"] = "cash_mismatch"
    operation = uuid4()
    with pytest.raises(DemoFailure):
        asyncio.run(exercise(c, operation, "LONG", monkeypatch))
    ledger = store.read("DEMO-test")
    assert ledger.reconciliation_settle_reason == "CASH_PROOF_MISMATCH"
    assert str(operation) not in ledger.initial_fill_settlements
    assert ledger.paused and ledger.risk_state.value == "EMERGENCY"
    assert fake.positions["BTCUSDT"] == 0 and not posts(fake, "/fapi/v1/algoOrder")
