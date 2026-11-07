"""Narrow readiness-only order views; original ledger and all other readers stay intact."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from packages.domain.contracts import OrderState, PositionState
from services.execution.demo_models import DemoLedger
from services.execution.models import PaperOrder
from services.execution.readiness_closed_history import INCIDENT, RECEIPT, readiness_view
from services.risk.paper import AccountValue
from tests.integration.test_demo import settings
from tests.integration.test_paper import signal, snapshot

NOW = datetime(2026, 11, 7, 1, tzinfo=UTC)


@pytest.fixture
def basis():
    from uuid import UUID, uuid5

    cfg = settings().model_copy(
        update={"demo_trading_enabled": False, "margin_mode": "ISOLATED", "symbols": ("BTCUSDT",)}
    )
    sig = signal(
        signal_id=UUID(INCIDENT), environment="DEMO", purpose="DEMO_VALIDATION", strategy=None
    )
    o = PaperOrder(
        client_id="DE-test",
        signal=sig,
        snapshot=snapshot(),
        group="BTC",
        quantity="0.0007",
        quantity_step="0.0001",
        price_tick="0.1",
        loss_per_unit="1",
        budget="1",
        state=OrderState.FILLED,
        position_state=PositionState.CLOSED,
        filled="0.0007",
        remaining="0",
        open_quantity="0",
        server_order_id="28668115919",
        algo_id="DS-test",
        tp_algo_id="DT-test",
    )
    original = o.model_copy(deep=True)
    original.position_state = PositionState.OPEN_UNPROTECTED
    original.open_quantity = original.filled
    receipt = {
        "incident": INCIDENT,
        "source": "SIGNED_OPERATOR_MANUAL_CLOSE",
        "entry_order_id": "28668115919",
        "manual_close_order_id": "28668119491",
        "trade_ids": ["554837511", "554838531"],
        "net_usdt": "-0.07032487",
        "original_order": original.model_dump(mode="json"),
        "manual_close_client_id": "web_synthetic_close",
        "exchange_fingerprint": "a" * 64,
        "prior_ledger_sha256": "b" * 64,
        "acknowledged_at_utc": (NOW - timedelta(seconds=30)).isoformat(),
    }
    ledger = DemoLedger(
        policy=cfg.policy,
        value=AccountValue(as_of=NOW - timedelta(minutes=1), initial="10000"),
        daily_base="10000",
        weekly_base="10000",
        high_water="10000",
        daily_key="2026-11-07",
        weekly_key="2026-11-02",
        margin_mode="ISOLATED",
        symbols=("BTCUSDT",),
        verified_leverage={"BTCUSDT": Decimal(2)},
        income_since_ms=1,
        orders={INCIDENT: o},
        operations={
            RECEIPT: json.dumps(receipt),
            str(uuid5(UUID(INCIDENT), "tp-trigger-observation")): "TP_TRIGGER:NOT_ACCEPTED",
        },
        paused=True,
        risk_state="NORMAL",
    )
    return cfg, ledger


def test_only_reviewed_receipt_bound_record_is_omitted_from_deep_copy(basis):
    cfg, ledger = basis
    before = ledger.model_dump_json()
    view, count = readiness_view(cfg, ledger, NOW)
    assert count == 1 and INCIDENT not in view.orders
    assert ledger.model_dump_json() == before and ledger.orders[INCIDENT].exit_id is None
    assert view.operations == ledger.operations and view.requests == ledger.requests
    assert (
        view.value == ledger.value
        and view.daily_base == ledger.daily_base
        and view.weekly_base == ledger.weekly_base
        and view.high_water == ledger.high_water
    )


@pytest.mark.parametrize(
    "fault",
    [
        "receipt_missing",
        "marker",
        "income_missing",
        "receipt_source",
        "receipt_json",
        "receipt_hash",
        "fee",
        "trade_ids",
        "close_order",
        "manual_id",
        "future_receipt",
        "new_financial_anchor",
        "original_client",
        "original_signal",
        "original_open",
        "original_partial",
        "original_protected",
        "entry_id",
        "algo_missing",
        "client_live",
        "unpaused",
        "local_position",
        "loss_lock",
    ],
)
def test_unproven_context_retains_original_query_input(basis, fault):
    cfg, s = basis
    r = json.loads(s.operations[RECEIPT])
    if fault == "marker":
        from uuid import UUID, uuid5

        s.operations[str(uuid5(UUID(INCIDENT), "tp-trigger-observation"))] = "TP_TRIGGER:ACCEPTED"
    elif fault == "income_missing":
        s.income_since_ms = None
    elif fault == "receipt_missing":
        s.operations.pop(RECEIPT)
    elif fault == "receipt_source":
        r["source"] = "OTHER"
    elif fault == "receipt_json":
        s.operations[RECEIPT] = "not-json"
    elif fault == "receipt_hash":
        r["prior_ledger_sha256"] = "bad"
    elif fault == "fee":
        r["net_usdt"] = "-0.07025287"
    elif fault == "trade_ids":
        r["trade_ids"] = ["other"]
    elif fault == "close_order":
        r["manual_close_order_id"] = "other"
    elif fault == "manual_id":
        r["manual_close_client_id"] = "api_close"
    elif fault == "future_receipt":
        r["acknowledged_at_utc"] = (NOW + timedelta(seconds=1)).isoformat()
    elif fault == "new_financial_anchor":
        s.value = s.value.model_copy(update={"as_of": NOW})
    elif fault == "original_client":
        r["original_order"]["client_id"] = "DE-other"
    elif fault == "original_signal":
        r["original_order"]["signal"]["strategy"] = "altered"
    elif fault == "original_open":
        r["original_order"]["open_quantity"] = "0"
    elif fault == "original_partial":
        r["original_order"]["filled"] = "0.0003"
    elif fault == "original_protected":
        r["original_order"]["protected"] = True
    elif fault == "entry_id":
        s.orders[INCIDENT].server_order_id = "other"
    elif fault == "algo_missing":
        s.orders[INCIDENT].tp_algo_id = None
    elif fault == "client_live":
        cfg = cfg.model_copy(update={"demo_trading_enabled": True})
    elif fault == "unpaused":
        s.paused = False
    elif fault == "local_position":
        s.positions["BTCUSDT"] = Decimal("0.0007")
    else:
        from packages.domain.contracts import RiskState

        s.risk_state = RiskState.DAILY_LOCK
    if fault not in {"receipt_missing", "receipt_json"}:
        s.operations[RECEIPT] = json.dumps(r)
    before = s.model_dump_json()
    view, count = readiness_view(cfg, s, NOW)
    assert count == 0 and view == s and s.model_dump_json() == before
