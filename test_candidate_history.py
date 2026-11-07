# ruff: noqa: F811
"""Offline candidate only; canonical runtime and installed source guards stay untouched."""

import importlib.util
import json
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from integrations.binance_usdm.demo_snapshot import operational_snapshot, snapshot
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_readiness_closed_history_cli import installed  # noqa: F401
from tests.unit.test_readiness_closed_history import INCIDENT, NOW, RECEIPT, basis  # noqa: F401

spec = importlib.util.spec_from_file_location(
    "operational_history_candidate", Path(__file__).with_name("candidate_snapshot.py")
)
candidate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(candidate)


def current_equal(old, new):
    return all(
        getattr(old, k) == getattr(new, k)
        for k in type(old).model_fields
        if k not in {"as_of", "fingerprint", "orders", "algos"}
    )


def test_only_closed_receipt_history_is_omitted_while_all_current_fields_remain_equal(installed):
    store, client, fake = installed
    ledger = store.read("DEMO-ready")
    original = ledger.model_dump_json()
    old_start = len(fake.calls)
    old = operational_snapshot(client, ledger, store.now())
    middle = len(fake.calls)
    new = candidate.operational_snapshot(client, ledger, store.now())
    assert len(fake.calls) - middle == middle - old_start - 3
    assert current_equal(old, new)
    target = ledger.orders[INCIDENT]
    assert {k: v for k, v in old.orders.items() if k != target.client_id} == new.orders
    assert {
        k: v for k, v in old.algos.items() if k not in {target.algo_id, target.tp_algo_id}
    } == new.algos
    assert ledger.model_dump_json() == original
    assert any(p.get("origClientOrderId") == "DE-unadopted" for _, _, p in fake.calls[middle:])


def test_current_valuation_can_advance_without_reclassifying_old_close_as_active(installed):
    store, client, fake = installed
    ledger = store.read("DEMO-ready")
    ledger.value = ledger.value.model_copy(update={"as_of": NOW + timedelta(minutes=1)})
    fake.at = NOW + timedelta(minutes=1)
    old = operational_snapshot(client, ledger, store.now())
    new = candidate.operational_snapshot(client, ledger, store.now())
    assert current_equal(old, new) and ledger.orders[INCIDENT].client_id not in new.orders


@pytest.mark.parametrize(
    "fault",
    [
        "receipt_missing",
        "bad_json",
        "wrong_source",
        "fee",
        "original_client",
        "marker",
        "open_quantity",
        "remaining",
        "protection",
        "exit_pending",
        "future_receipt",
    ],
)
def test_ambiguous_or_live_target_record_never_uses_closed_receipt_shortcut(installed, fault):
    from decimal import Decimal

    from packages.domain.contracts import PositionState

    store, client, fake = installed
    ledger = store.read("DEMO-ready")
    o = ledger.orders[INCIDENT]
    r = json.loads(ledger.operations[RECEIPT])
    if fault == "receipt_missing":
        ledger.operations.pop(RECEIPT)
    elif fault == "bad_json":
        ledger.operations[RECEIPT] = "bad-json"
    elif fault == "wrong_source":
        r["source"] = "OTHER"
    elif fault == "fee":
        r["net_usdt"] = "-0.07025287"
    elif fault == "original_client":
        r["original_order"]["client_id"] = "DE-OTHER"
    elif fault == "marker":
        from uuid import UUID, uuid5

        ledger.operations[str(uuid5(UUID(INCIDENT), "tp-trigger-observation"))] = (
            "TP_TRIGGER:VERIFIED"
        )
    elif fault == "open_quantity":
        o.open_quantity = Decimal("0.0007")
    elif fault == "remaining":
        o.remaining = Decimal("0.0001")
    elif fault == "protection":
        o.protected = True
    elif fault == "exit_pending":
        o.position_state = PositionState.EXIT_PENDING
    else:
        r["acknowledged_at_utc"] = (store.now() + timedelta(days=1)).isoformat()
    if fault not in {"receipt_missing", "bad_json"}:
        ledger.operations[RECEIPT] = json.dumps(r)
    old = operational_snapshot(client, ledger, store.now())
    new = candidate.operational_snapshot(client, ledger, store.now())
    assert new.model_dump(exclude={"as_of"}) == old.model_dump(exclude={"as_of"})


@pytest.mark.parametrize("reappearing", ["entry", "stop", "target"])
def test_target_reappearing_in_either_fresh_open_list_is_not_omitted(installed, reappearing):
    store, client, fake = installed
    ledger = store.read("DEMO-ready")
    if reappearing == "entry":
        fake.orders["DE-test"]["status"] = "NEW"
    else:
        fake.algos["DS-test" if reappearing == "stop" else "DT-test"]["algoStatus"] = "NEW"
    old = operational_snapshot(client, ledger, store.now())
    new = candidate.operational_snapshot(client, ledger, store.now())
    assert new.model_dump(exclude={"as_of"}) == old.model_dump(exclude={"as_of"})


def test_full_historical_snapshot_keeps_every_original_query_and_same_result(installed):
    store, client, fake = installed
    ledger = store.read("DEMO-ready")
    old = snapshot(client, ledger, store.now())
    start = len(fake.calls)
    new = candidate.snapshot(client, ledger, store.now())
    assert new.model_dump(exclude={"as_of"}) == old.model_dump(exclude={"as_of"})
    assert sum(p.get("origClientOrderId") == "DE-test" for _, _, p in fake.calls[start:]) == 1
    assert sum(p.get("clientAlgoId") == "DS-test" for _, _, p in fake.calls[start:]) == 1
    assert sum(p.get("clientAlgoId") == "DT-test" for _, _, p in fake.calls[start:]) == 1


@pytest.mark.parametrize("state", ["protected", "partial", "unprotected", "exit_pending"])
def test_active_peer_and_partial_protection_reads_remain_identical(installed, state):
    from decimal import Decimal

    from packages.domain.contracts import OrderState, PositionState

    store, client, fake = installed
    ledger = store.read("DEMO-ready")
    client.settings = client.settings.model_copy(update={"demo_trading_enabled": True})
    active = ledger.orders[INCIDENT].model_copy(deep=True)
    identity = uuid4()
    active.signal = active.signal.model_copy(update={"signal_id": identity})
    active.client_id = "DE-active"
    active.server_order_id = "12345"
    active.algo_id = "DS-active"
    active.tp_algo_id = "DT-active"
    active.average_price = Decimal("100")
    active.open_quantity = active.filled
    active.protected = state not in {"unprotected", "exit_pending"}
    active.position_state = (
        PositionState.PROTECTED if active.protected else PositionState.OPEN_UNPROTECTED
    )
    if state == "partial":
        active.state = OrderState.PARTIALLY_FILLED
        active.open_quantity = active.filled = Decimal("0.0003")
        active.remaining = Decimal("0.0004")
    elif state == "exit_pending":
        active.position_state = PositionState.EXIT_PENDING
        active.exit_id = "DX-active"
        active.exit_state = "UNKNOWN"
    ledger.orders[str(identity)] = active
    fake.orders[active.client_id] = dict(
        fake.orders["DE-test"],
        clientOrderId=active.client_id,
        orderId="12345",
        status=active.state.value,
        executedQty=str(active.filled),
    )
    for old, new in [("DS-test", "DS-active"), ("DT-test", "DT-active")]:
        fake.algos[new] = dict(
            fake.algos[old], clientAlgoId=new, algoId=new + "1", algoStatus="NEW"
        )
    fake.positions["BTCUSDT"] = active.open_quantity
    fake.prices["BTCUSDT"] = Decimal("100")
    ledger.positions["BTCUSDT"] = active.open_quantity
    begin = len(fake.calls)
    old = operational_snapshot(client, ledger, store.now())
    middle = len(fake.calls)
    new = candidate.operational_snapshot(client, ledger, store.now())
    assert current_equal(old, new)
    assert len(fake.calls) - middle == middle - begin - 3
    old_active = [
        (m, p, q)
        for m, p, q in fake.calls[begin:middle]
        if any(v in {"DE-active", "DS-active", "DT-active", "DX-active"} for v in q.values())
    ]
    new_active = [
        (m, p, q)
        for m, p, q in fake.calls[middle:]
        if any(v in {"DE-active", "DS-active", "DT-active", "DX-active"} for v in q.values())
    ]
    assert old_active == new_active
    assert new.algos["DS-active"]["algoStatus"] == new.algos["DT-active"]["algoStatus"] == "NEW"
    assert new.positions["BTCUSDT"] == active.open_quantity


def test_old_history_omission_does_not_clear_a_current_loss_lock(installed):
    from packages.domain.contracts import RiskState

    store, client, _ = installed
    ledger = store.read("DEMO-ready")
    ledger.risk_state = RiskState.DAILY_LOCK
    before = ledger.model_dump_json()
    candidate.operational_snapshot(client, ledger, store.now())
    assert ledger.risk_state == RiskState.DAILY_LOCK and ledger.model_dump_json() == before


def test_parser_structure_preserves_current_account_income_and_proof_logic():
    from verify_structure import verify

    result = verify(Path.cwd())
    assert result["snapshot_parser_identical_after_removing_one_closed_receipt_condition"]


def test_approved_install_source_guard_still_accepts_only_approved_runtime():
    spec = importlib.util.spec_from_file_location(
        "approved_guard", Path("guard_readiness_history_install.py")
    )
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    guard.verify_sources("post")
