"""Original-ID evidence is tightly scoped and never grants reconciliation authority."""

from types import SimpleNamespace

import pytest

from packages.domain.contracts import OrderState, PositionState
from scripts import probe_demo_accounted_reads as common
from scripts import review_demo_historical_requests as history
from services.execution.models import PaperOrder
from tests.integration.test_historical_request_evidence import response
from tests.integration.test_paper import signal, snapshot


@pytest.fixture
def ledger():
    orders = {}
    for i in range(34):
        orders[str(i)] = PaperOrder(
            client_id=f"entry-{i}",
            signal=signal(environment="DEMO", purpose="DEMO_VALIDATION", strategy=None),
            snapshot=snapshot(),
            group="BTC",
            quantity="1",
            quantity_step=".1",
            price_tick=".1",
            loss_per_unit="1",
            budget="1",
            remaining="0",
            state=OrderState.FILLED,
            position_state=PositionState.CLOSED,
            algo_id=f"sl-{i}",
            exit_id=f"exit-{i}",
        )
    return SimpleNamespace(
        orders=orders,
        requests={
            "entry-0": "UNKNOWN",
            "entry-1": "UNKNOWN",
            "sl-2": "UNKNOWN",
            "exit-2": "SUBMITTING",
        },
        algo_server_ids={},
    )


def test_plans_exactly_four_installed_get_shapes_without_mutations(ledger):
    plan = history.plan_requests(ledger, common)
    assert [p["role"] for p in plan] == ["ENTRY", "ENTRY", "STOP_LOSS", "EXIT"]
    assert all(
        set(p["params"])
        == ({"clientAlgoId"} if p["role"] == "STOP_LOSS" else {"symbol", "origClientOrderId"})
        for p in plan
    )
    assert all("identifier" not in p and "params" not in p for p in history.public_plan(plan))
    assert len(ledger.orders) == 34 and ledger.requests["entry-0"] == "UNKNOWN"


@pytest.mark.parametrize(
    "change",
    ["extra", "unmatched", "shared-id", "symbol", "open", "live", "history-count", "role", "state"],
)
def test_unreviewed_or_ambiguous_request_sets_are_refused(ledger, change):
    if change == "extra":
        ledger.requests["entry-3"] = "UNKNOWN"
    elif change == "unmatched":
        ledger.requests["alien"] = "UNKNOWN"
        del ledger.requests["entry-0"]
    elif change == "shared-id":
        ledger.orders["3"].exit_id = "entry-0"
    elif change == "symbol":
        ledger.orders["0"].signal = ledger.orders["0"].signal.model_copy(
            update={"symbol": "ETHUSDT"}
        )
    elif change == "open":
        ledger.orders["0"].open_quantity = 1
    elif change == "live":
        ledger.orders["0"] = ledger.orders["0"].model_copy(
            update={"signal": ledger.orders["0"].signal.model_copy(update={"environment": "LIVE"})}
        )
    elif change == "history-count":
        ledger.orders.pop("33")
    elif change == "role":
        ledger.requests["exit-2"] = "UNKNOWN"
    else:
        ledger.requests["sl-2"] = "ACKNOWLEDGED"
    with pytest.raises(common.ProbeBlocked):
        history.plan_requests(ledger, common)


@pytest.mark.parametrize("role", ["ENTRY", "STOP_LOSS", "EXIT"])
def test_valid_responses_remain_observations_only(ledger, role):
    item = next(p for p in history.plan_requests(ledger, common) if p["role"] == role)
    result = history.observation(item, response(item), common)
    assert result["response_identity_fields_match"]
    assert not result["remote_resolution_verified"] and not result["automatically_resolvable"]
    assert not result["response_body_bound_to_audit_chain"]
    assert item["identifier"] not in str(result)


@pytest.mark.parametrize(
    "field,value",
    [
        ("clientOrderId", "alien"),
        ("symbol", "ETHUSDT"),
        ("side", "SELL"),
        ("positionSide", "LONG"),
        ("type", "MARKET"),
        ("reduceOnly", True),
        ("origQty", "2"),
        ("executedQty", "2"),
        ("executedQty", "NaN"),
        ("price", "101"),
        ("orderId", True),
        ("status", "ALIEN"),
    ],
)
def test_entry_identity_terms_and_numeric_mismatches_stop(ledger, field, value):
    item = history.plan_requests(ledger, common)[0]
    body = response(item)
    body[field] = value
    with pytest.raises(common.ProbeBlocked):
        history.observation(item, body, common)


@pytest.mark.parametrize(
    "field,value",
    [
        ("clientAlgoId", "alien"),
        ("side", "BUY"),
        ("triggerPrice", "91"),
        ("closePosition", False),
        ("priceProtect", True),
        ("workingType", "CONTRACT_PRICE"),
        ("orderType", "TAKE_PROFIT_MARKET"),
        ("algoType", "OTHER"),
        ("algoId", 0),
        ("algoStatus", "ALIEN"),
    ],
)
def test_algo_never_uses_regular_order_or_fuzzy_identity(ledger, field, value):
    item = next(p for p in history.plan_requests(ledger, common) if p["role"] == "STOP_LOSS")
    body = response(item)
    body[field] = value
    with pytest.raises(common.ProbeBlocked):
        history.observation(item, body, common)


def test_pinned_algo_server_id_must_also_match(ledger):
    ledger.algo_server_ids["sl-2"] = "99"
    item = next(p for p in history.plan_requests(ledger, common) if p["role"] == "STOP_LOSS")
    with pytest.raises(common.ProbeBlocked, match="SERVER_ID_MISMATCH"):
        history.observation(item, response(item), common)
