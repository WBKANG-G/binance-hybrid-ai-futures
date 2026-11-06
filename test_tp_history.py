import json
import sys
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from integrations.binance_usdm.demo import DemoClient, DemoRateLimited
from integrations.binance_usdm.rest import RESTResponse
from packages.domain.contracts import OrderState, PositionState, RiskState
from packages.persistence.demo import accounts, events
from services.execution.models import PaperOrder
from sqlalchemy import select
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_paper import signal, snapshot

import review_tp_history as m


@pytest.fixture
def case(demo_system):  # noqa: F811
    store, c, _ = demo_system
    now = store.now()
    sig = signal(
        signal_id=uuid4(),
        environment="DEMO",
        purpose="DEMO_VALIDATION",
        strategy=None,
        signal_closed_candle_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(minutes=1),
        suggested_limit_price="80000",
        initial_stop_price="79600",
        target_price="80800",
    )
    order = PaperOrder(
        client_id="DE-owned",
        signal=sig,
        snapshot=snapshot(),
        group="SYNTHETIC",
        quantity="0.0007",
        quantity_step="0.0001",
        price_tick="0.1",
        loss_per_unit="400",
        budget="0.3",
        remaining="0",
        filled="0.0007",
        state=OrderState.FILLED,
        position_state=PositionState.CLOSED,
        algo_id="DS-owned",
        tp_algo_id="DT-owned",
        server_order_id="100",
    )

    def bind(state):
        state.orders = {str(sig.signal_id): order}
        state.positions = {}
        state.paused = True
        state.risk_state = RiskState.NORMAL

    store.change(c.lease, "TEST_HISTORICAL_COHORT", bind)
    store.release(c.lease)
    rows = {}
    for cid, kind, price, status in [
        ("DT-owned", "TAKE_PROFIT_MARKET", "80800", "FINISHED"),
        ("DS-owned", "STOP_MARKET", "79600", "CANCELED"),
    ]:
        rows[cid] = {
            "clientAlgoId": cid,
            "symbol": "BTCUSDT",
            "side": "SELL",
            "positionSide": "BOTH",
            "algoType": "CONDITIONAL",
            "orderType": kind,
            "workingType": "MARK_PRICE",
            "closePosition": True,
            "priceProtect": False,
            "triggerPrice": price,
            "algoStatus": status,
            "actualOrderId": "901" if cid.startswith("DT") else "0",
        }
    rows["901"] = {
        "orderId": "901",
        "symbol": "BTCUSDT",
        "side": "SELL",
        "positionSide": "BOTH",
        "type": "MARKET",
        "status": "FILLED",
        "reduceOnly": True,
        "executedQty": "0.0007",
        "avgPrice": "80800",
    }
    calls = []

    def send(method, path, query, api_key):
        from urllib.parse import parse_qs

        params = {k: v[0] for k, v in parse_qs(query.get_secret_value()).items()}
        calls.append((method, path))
        assert method == "GET"
        if path == "/fapi/v1/time":
            assert api_key is None
            body = {"serverTime": int(now.timestamp() * 1000)}
        else:
            key = params.get("clientAlgoId", params.get("orderId"))
            body = rows[key]
            if body is None:
                return RESTResponse(400, b'{"code":-2013}', {})
            if body == "RATE":
                return RESTResponse(429, b'{"code":-1003}', {})
        return RESTResponse(200, json.dumps(body).encode(), {})

    source = DemoClient(
        c.client.settings.model_copy(update={"demo_trading_enabled": False}),
        c.client.credentials,
        send=send,
        clock_ms=lambda: int(now.timestamp() * 1000),
    )
    return store, source, rows, calls, order


def test_real_read_only_review_proves_execution_but_never_historical_cleanup_order(case):
    store, source, _, calls, _ = case

    def db_rows():
        with store.engine.connect() as conn:
            return conn.execute(select(accounts)).mappings().all(), conn.execute(
                select(events)
            ).mappings().all()

    before = db_rows()
    report = m.review(store.engine, source, "DEMO-test")
    assert report["signed_historical_tp_execution_count"] == 1
    assert report["t06_ordering_verified"] is False
    assert report["rows"][0]["original_stop_status"] == "CANCELED"
    assert report["signed_queries"] == 3 and report["selected_orders"] == 1
    assert not report["database_changed"] and not report["lease_acquired"]
    assert report["order_writes"] == 0 and not report["signal_generated"]
    assert db_rows() == before and len(calls) == 4
    assert "DT-owned" not in json.dumps(report)
    assert "actualOrderId" not in json.dumps(report) and "clientAlgoId" not in json.dumps(report)


@pytest.mark.parametrize("where", ["DT-owned", "DS-owned", "901"])
def test_not_found_never_becomes_never_filled_or_cleanup_proof(case, where):
    store, source, rows, _, _ = case
    rows[where] = None
    report = m.review(store.engine, source, "DEMO-test")
    assert report["t06_ordering_verified"] is False
    if where != "DS-owned":
        assert report["signed_historical_tp_execution_count"] == 0
    else:
        assert report["rows"][0]["original_stop_status"] == "NOT_FOUND_UNRESOLVED"


@pytest.mark.parametrize(
    "field,value",
    [
        ("orderId", "902"),
        ("symbol", "ETHUSDT"),
        ("side", "BUY"),
        ("positionSide", "LONG"),
        ("type", "LIMIT"),
        ("status", "NEW"),
        ("reduceOnly", False),
        ("executedQty", "0.0006"),
        ("avgPrice", "0"),
    ],
)
def test_wrong_execution_never_counts_as_actual_tp_fill(case, field, value):
    store, source, rows, _, _ = case
    rows["901"][field] = value
    report = m.review(store.engine, source, "DEMO-test")
    assert report["signed_historical_tp_execution_count"] == 0
    assert report["rows"][0]["tp_execution_evidence"] == "EXECUTION_MISMATCH"


@pytest.mark.parametrize("status", ["NEW", "EXPIRED", "CANCELED", "REJECTED"])
def test_nonfinished_algo_cannot_prove_tp_execution(case, status):
    store, source, rows, calls, _ = case
    rows["DT-owned"]["algoStatus"] = status
    report = m.review(store.engine, source, "DEMO-test")
    assert report["signed_historical_tp_execution_count"] == 0
    assert all(path != "/fapi/v1/order" for _, path in calls)


@pytest.mark.parametrize("actual", ["", "0", "-1", "invalid"])
def test_missing_actual_order_id_stays_unverified(case, actual):
    store, source, rows, _, _ = case
    rows["DT-owned"]["actualOrderId"] = actual
    report = m.review(store.engine, source, "DEMO-test")
    assert report["rows"][0]["tp_execution_evidence"] == "EXECUTION_ID_UNAVAILABLE"


@pytest.mark.parametrize(
    "field,value",
    [
        ("clientAlgoId", "DT-foreign"),
        ("side", "BUY"),
        ("triggerPrice", "80801"),
        ("closePosition", False),
        ("priceProtect", True),
    ],
)
def test_algo_identity_or_protection_mismatch_blocks(case, field, value):
    store, source, rows, _, _ = case
    rows["DT-owned"][field] = value
    with pytest.raises(ValueError):
        m.review(store.engine, source, "DEMO-test")


@pytest.mark.parametrize(
    "method,path,params",
    [
        ("POST", "/fapi/v1/order", {}),
        ("DELETE", "/fapi/v1/algoOrder", {"clientAlgoId": "DS-owned"}),
        ("GET", "/fapi/v1/trade/asyn", {}),
        ("GET", "/fapi/v3/account", {}),
        ("GET", "/fapi/v1/algoOrder", {"clientAlgoId": "DT-foreign"}),
        ("GET", "/fapi/v1/order", {"symbol": "BTCUSDT", "orderId": "901"}),
    ],
)
def test_unapproved_routes_or_execution_id_rejected_before_transport(case, method, path, params):
    _, source, _, calls, _ = case
    reader = m.ScopedReader(source, {"DT-owned", "DS-owned"})
    with pytest.raises(ValueError):
        reader.request(method, path, params)
    assert calls == []


def test_private_session_query_budget_and_write_enabled_source_rejected(case):
    _, source, _, calls, _ = case
    reader = m.ScopedReader(source, {"DT-owned"})
    with pytest.raises(ValueError):
        reader.listen_key("POST")
    reader.budget.signed_queries = 36
    with pytest.raises(ValueError):
        reader.request("GET", "/fapi/v1/algoOrder", {"clientAlgoId": "DT-owned"})
    source.settings = source.settings.model_copy(update={"demo_trading_enabled": True})
    with pytest.raises(Exception, match="read-only"):
        m.ScopedReader(source, set())
    assert not calls


def test_rate_limit_not_retried(case):
    store, source, rows, calls, _ = case
    rows["DT-owned"] = "RATE"
    with pytest.raises(DemoRateLimited):
        m.review(store.engine, source, "DEMO-test")
    assert len(calls) == 2


def test_selection_bound_and_filter(case):
    store, _, _, _, order = case
    ledger = store.read("DEMO-test")
    ledger.orders = {}
    for index in range(14):
        item = order.model_copy(deep=True)
        item.signal = item.signal.model_copy(
            update={
                "signal_id": uuid4(),
                "signal_closed_candle_at": item.signal.signal_closed_candle_at
                + timedelta(seconds=index),
            }
        )
        ledger.orders[str(item.signal.signal_id)] = item
    picked, total = m.selected(ledger)
    assert total == 14 and len(picked) == 12
    assert picked == sorted(picked, key=lambda o: o.signal.signal_closed_candle_at, reverse=True)
    picked[0].signal = picked[0].signal.model_copy(
        update={
            "direction": "SHORT",
            "initial_stop_price": Decimal("80800"),
            "target_price": Decimal("79600"),
        }
    )
    ledger.orders[str(picked[1].signal.signal_id)] = picked[1].model_copy(
        update={"filled": Decimal(0), "state": OrderState.CANCELED}
    )
    _, total = m.selected(ledger)
    assert total == 12


def test_changed_account_state_cannot_produce_completed_review(case, monkeypatch):
    store, source, _, _, _ = case
    real = m.read_state
    calls = []

    def changed(*args):
        data = real(*args)
        calls.append(True)
        return data if len(calls) == 1 else (data[0], data[1] + 1, data[2], data[3])

    monkeypatch.setattr(m, "read_state", changed)
    with pytest.raises(ValueError):
        m.review(store.engine, source, "DEMO-test")


def test_cli_requires_review_only_before_credentials(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["review"])
    monkeypatch.setattr(m, "credentials", lambda **_: pytest.fail("must not read credentials"))
    with pytest.raises(SystemExit) as error:
        m.main()
    assert error.value.code == 2


@pytest.mark.parametrize("fault", ["active_owner", "not_paused", "emergency", "exposure"])
def test_unstable_or_exposed_local_state_blocks_before_transport(case, fault):
    store, source, _, calls, order = case
    lease = store.acquire("DEMO-test", uuid4())

    def change(state):
        if fault == "not_paused":
            state.paused = False
        elif fault == "emergency":
            state.risk_state = RiskState.EMERGENCY
        elif fault == "exposure":
            state.orders[str(order.signal.signal_id)].open_quantity = Decimal("0.0007")

    store.change(lease, "TEST_HISTORY_SCOPE_BLOCK", change)
    if fault != "active_owner":
        store.release(lease)
    try:
        with pytest.raises(ValueError):
            m.review(store.engine, source, "DEMO-test")
        assert not calls
    finally:
        if fault == "active_owner":
            store.release(lease)


def test_duplicate_local_protection_ids_block_before_transport(case):
    store, source, _, calls, order = case
    lease = store.acquire("DEMO-test", uuid4())

    def duplicate(state):
        item = order.model_copy(deep=True)
        item.signal = item.signal.model_copy(update={"signal_id": uuid4()})
        state.orders[str(item.signal.signal_id)] = item

    store.change(lease, "TEST_AMBIGUOUS_HISTORY_IDS", duplicate)
    store.release(lease)
    with pytest.raises(ValueError):
        m.review(store.engine, source, "DEMO-test")
    assert not calls


def test_original_time_budget_stops_before_transport_and_restores_source(case, monkeypatch):
    from services.execution.demo_recovery_budget import AuditBudgetExceeded

    store, source, _, calls, _ = case
    original_send = source.send

    class Expired(m.ScopedReader):
        def __init__(self, *args):
            super().__init__(*args)
            self.budget.clock = lambda: self.budget.started + 41

    monkeypatch.setattr(m, "ScopedReader", Expired)
    with pytest.raises(AuditBudgetExceeded):
        m.review(store.engine, source, "DEMO-test")
    assert not calls and source.send is original_send


def test_cli_exception_is_redacted(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["review", "--review-only"])

    def fail(**_):
        raise RuntimeError("PRIVATE_CREDENTIAL_EXAMPLE")

    monkeypatch.setattr(m, "credentials", fail)
    with pytest.raises(SystemExit) as error:
        m.main()
    assert error.value.code == 2
    out = capsys.readouterr().out
    assert "PRIVATE_CREDENTIAL_EXAMPLE" not in out
    assert json.loads(out)["order_writes"] == 0
