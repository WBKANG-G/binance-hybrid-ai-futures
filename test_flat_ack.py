# ruff: noqa: F811
import copy
import importlib.util
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path
from uuid import UUID, uuid4, uuid5

import pytest
from packages.domain.contracts import OrderState, PositionState
from services.execution.demo_models import PrivateSessionDiagnostic
from services.execution.models import PaperOrder
from services.risk.paper import period_keys
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_recovery import recovery  # noqa: F401
from tests.integration.test_paper import signal, snapshot

spec = importlib.util.spec_from_file_location(
    "ack_wrapper", Path(__file__).with_name("acknowledge_flat_recovery.py")
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


@pytest.fixture
def approved(recovery, monkeypatch):
    store, c, fake = recovery
    store.release(c.lease)
    fake.at = datetime(2026, 11, 6, 12, tzinfo=UTC)
    lease = store.acquire("DEMO-test", uuid4())
    c.client.sync_clock()
    zerosig = UUID("b8454548-08d4-4cc3-9e77-d411b5659eff")
    ids = [
        (
            zerosig,
            "DE-zero",
            "DX-historical",
            "DS-historical",
            None,
            False,
            datetime(2026, 10, 20, 13, 49, 59, 999000, tzinfo=UTC),
        ),
        (
            UUID("77db044e-1bcd-4358-80e6-df9730630213"),
            "DE-stop",
            "DX-stop",
            "DS-stop-not-sent",
            None,
            True,
            datetime(2026, 10, 31, 14, 21, 59, 999000, tzinfo=UTC),
        ),
        (
            UUID("426dd7a2-805b-4576-a1f4-85313a7c4f6c"),
            "DE-target",
            "DX-target",
            None,
            "DT-target-not-sent",
            True,
            datetime(2026, 10, 31, 10, 57, 59, 999000, tzinfo=UTC),
        ),
        (
            m.LATEST_OPERATION,
            "DE-latest",
            "DX-latest",
            "DS-latest",
            "DT-latest",
            True,
            fake.at - timedelta(minutes=1),
        ),
    ]
    orders = {}
    for index, (op, entry, exit_id, stop, target, filled, at) in enumerate(ids):
        sig = signal(
            environment="DEMO",
            purpose="DEMO_VALIDATION",
            strategy=None,
            signal_id=op,
            signal_closed_candle_at=at,
            expires_at=at + timedelta(minutes=2),
            suggested_limit_price="80000",
            initial_stop_price="79600",
            target_price="80800",
        )
        order = PaperOrder(
            client_id=entry,
            signal=sig,
            snapshot=snapshot(),
            group="SYNTHETIC",
            quantity="0.0007",
            quantity_step="0.0001",
            price_tick="0.1",
            loss_per_unit="400",
            budget="0.3",
            remaining="0",
            filled="0.0007" if filled else "0",
            state=OrderState.FILLED if filled else OrderState.CANCELED,
            position_state=PositionState.CLOSED,
            exit_id=exit_id,
            exit_state="FILLED" if filled else "UNKNOWN",
            exit_generation=1 if filled else 0,
            algo_id=stop,
            tp_algo_id=target,
            server_order_id=str(index * 2 + 1),
        )
        orders[str(op)] = order
        fake.orders[entry] = dict(
            symbol="BTCUSDT",
            clientOrderId=entry,
            orderId=str(index * 2 + 1),
            side="BUY",
            type="LIMIT",
            status="FILLED" if filled else "CANCELED",
            origQty="0.0007",
            executedQty="0.0007" if filled else "0",
            avgPrice="80000" if filled else "0",
            price="80000",
            positionSide="BOTH",
            reduceOnly=False,
        )
        if filled:
            fake.orders[exit_id] = dict(
                symbol="BTCUSDT",
                clientOrderId=exit_id,
                orderId=str(index * 2 + 2),
                side="SELL",
                type="MARKET",
                status="FILLED",
                origQty="0.0007",
                executedQty="0.0007",
                avgPrice="80000",
                price="0",
                positionSide="BOTH",
                reduceOnly=True,
            )

    for cid, kind, price in [
        ("DS-latest", "STOP_MARKET", "79600"),
        ("DT-latest", "TAKE_PROFIT_MARKET", "80800"),
    ]:
        fake.algos[cid] = dict(
            symbol="BTCUSDT",
            clientAlgoId=cid,
            algoId="901" if kind == "STOP_MARKET" else "902",
            side="SELL",
            orderType=kind,
            algoStatus="EXPIRED",
            triggerPrice=price,
            closePosition=True,
            positionSide="BOTH",
            workingType="MARK_PRICE",
            actualOrderId="",
            priceProtect=False,
            algoType="CONDITIONAL",
        )

    def bind(ledger):
        ledger.orders = orders
        ledger.daily_key, ledger.weekly_key = period_keys(fake.at, ledger.policy.week_start_kst)
        ledger.requests = {
            "DS-historical": "UNKNOWN",
            "DX-historical": "SUBMITTING",
            "DS-stop-not-sent": "NOT_SENT",
            "DT-target-not-sent": "NOT_SENT",
            **{cid: row["status"] for cid, row in fake.orders.items()},
            "DS-latest": "VERIFIED",
            "DT-latest": "VERIFIED",
        }
        ledger.request_no_send_evidence = {
            "DS-stop-not-sent": "LOCAL_VALIDATION_FAILED",
            "DT-target-not-sent": "LOCAL_VALIDATION_FAILED",
        }
        ledger.operations[str(uuid5(m.LATEST_OPERATION, "protected-worker-finish-exit"))] = (
            "EMERGENCY_FLATTEN"
        )

        ledger.operations[str(uuid5(m.LATEST_OPERATION, "protected-worker-finally"))] = (
            "FINALLY_RAN"
        )
        ledger.last_private_session_failure = PrivateSessionDiagnostic(
            stage="EVENT", code="DB_LOCK_TIMEOUT", event_kind="ALGO_UPDATE"
        )
        ledger.validation_evidence[str(m.LATEST_OPERATION)] = {
            "stop_verified": True,
            "target_verified": True,
        }
        ledger.recovery_receipts["prior-confirmation"] = "SYNTHETIC_PRIOR_RECEIPT_PRESERVED"

    store.change(lease, "TEST_APPROVED_HISTORY", bind)
    reference = m.details.inspect(store, c.client, "DEMO-test")
    m.validate_reference(reference)
    reference["audited_request_count"] -= 4  # Original report before latest entry/SL/TP/exit.
    store.release(lease)

    def ready():
        ledger = store.read("DEMO-test")
        assert ledger.risk_state.value == "NORMAL" and ledger.paused
        assert m.details.row(store.engine, "DEMO-test")["owner"] is None
        return {
            "session_ready": "READ_ONLY_READY_ENTRIES_STILL_PAUSED",
            "risk_state": "NORMAL",
            "paused": True,
            "orders_enabled": False,
            "live_trading_enabled": False,
        }

    monkeypatch.setattr(m, "ready_check", ready)
    return store, c, fake, reference


def test_real_canonical_apply_preserves_four_history_and_idempotent_reuse(approved):
    store, c, fake, reference = approved
    before = store.read("DEMO-test")
    count = len(fake.calls)
    result = m.execute(store, c.client, reference, acknowledged=True, account="DEMO-test")
    assert result["recovery"] == "EMERGENCY_ACKNOWLEDGED_READY_ENTRIES_STILL_PAUSED", result
    assert result["leader_released"] and result["unresolved_history_acknowledged"]
    assert result["unresolved_statuses_cleared"] is False
    after = store.read("DEMO-test")
    assert after.requests == before.requests and after.orders == before.orders
    assert (
        after.value == before.value
        and after.daily_base == before.daily_base
        and after.weekly_base == before.weekly_base
    )
    assert (
        after.high_water == before.high_water
        and after.paused
        and after.risk_state.value == "NORMAL"
    )
    assert all(method == "GET" and "asyn" not in path for method, path, _ in fake.calls[count:])
    count = len(fake.calls)
    repeated = m.execute(store, c.client, reference, acknowledged=True, account="DEMO-test")
    assert repeated["recovery"] == "ALREADY_ACKNOWLEDGED_READY_ENTRIES_STILL_PAUSED"
    assert len(fake.calls) == count


@pytest.mark.parametrize(
    "fault", ["no_ack", "digest", "count", "new_operation", "local_evidence", "extra_exposure"]
)
def test_changed_reference_or_no_ack_never_applies(approved, fault):
    store, c, _, ref = approved
    changed = copy.deepcopy(ref)
    if fault == "digest":
        changed["unresolved_set_digest"] = "0" * 64
    elif fault == "count":
        changed["unresolved_count"] = 3
    elif fault == "new_operation":
        changed["unresolved_requests"][0]["linked_orders"][0]["is_latest_opposite_tp"] = True
    elif fault == "local_evidence":
        for r in changed["unresolved_requests"]:
            if r["local_request_status"] == "NOT_SENT":
                r["local_no_send_evidence"] = None
    elif fault == "extra_exposure":
        changed["active_orders"] = 1
    before = store.read("DEMO-test")
    result = m.execute(
        store, c.client, changed, acknowledged=fault != "no_ack", account="DEMO-test"
    )
    assert result["recovery"] == "BLOCKED_KEEP_PAUSED_NO_AUTOMATIC_RETRY"
    assert result["application_may_have_completed"] is False
    assert store.read("DEMO-test") == before


def test_new_remote_position_blocks_apply(approved):
    store, c, fake, ref = approved
    fake.positions["BTCUSDT"] = D("0.0007")
    fake.prices["BTCUSDT"] = D("80000")
    before = store.read("DEMO-test")
    result = m.execute(store, c.client, ref, acknowledged=True, account="DEMO-test")
    assert result["recovery"] == "BLOCKED_KEEP_PAUSED_NO_AUTOMATIC_RETRY"
    assert not result["application_may_have_completed"] and store.read("DEMO-test") == before


def test_canonical_token_change_between_reviews_does_not_unlock(approved, monkeypatch):
    store, c, _, ref = approved
    original = m.recovery.acknowledge_flat_emergency

    def changed(store, lease, client, op, token, *, acknowledge_unresolved):
        return original(
            store, lease, client, op, "0" * 64, acknowledge_unresolved=acknowledge_unresolved
        )

    monkeypatch.setattr(m.recovery, "acknowledge_flat_emergency", changed)
    before = store.read("DEMO-test")
    result = m.execute(store, c.client, ref, acknowledged=True, account="DEMO-test")
    assert result["recovery"] == "BLOCKED_KEEP_PAUSED_NO_AUTOMATIC_RETRY"
    assert result["error_code"] == "REVIEW_TOKEN_CHANGED"
    after = store.read("DEMO-test")
    assert after.risk_state.value == "EMERGENCY" and after.requests == before.requests
    assert str(m.OPERATION) not in after.recovery_receipts and result["leader_released"]


def test_post_apply_failure_reports_possible_completion_and_never_retries(approved, monkeypatch):
    store, c, _, ref = approved

    def fail():
        raise ValueError("PRIVATE_RESPONSE")

    monkeypatch.setattr(m, "ready_check", fail)
    result = m.execute(store, c.client, ref, acknowledged=True, account="DEMO-test")
    assert result["recovery"] == "BLOCKED_KEEP_PAUSED_NO_AUTOMATIC_RETRY"
    assert result["stage"] == "POST_APPLY_READY" and result["application_may_have_completed"]
    assert "PRIVATE_RESPONSE" not in json.dumps(result)
    assert store.read("DEMO-test").risk_state.value == "NORMAL"
    assert store.read("DEMO-test").paused and result["leader_released"]


@pytest.mark.parametrize(
    "fault",
    [
        "old_finish_marker",
        "ready_marker",
        "no_finally",
        "wrong_failure",
        "unverified_stop",
        "extra_request",
        "wrong_audit_delta",
        "extra_exit_generation",
        "target_evidence_missing",
    ],
)
def test_new_incident_binding_and_exact_four_request_delta(approved, fault):
    from services.execution.demo_models import PrivateSessionDiagnostic

    store, c, _, reference = approved
    if fault == "wrong_audit_delta":
        reference = copy.deepcopy(reference)
        reference["audited_request_count"] -= 1
    else:
        lease = store.acquire("DEMO-test", uuid4())

        def change(state):
            if fault == "old_finish_marker":
                state.operations.pop(str(uuid5(m.LATEST_OPERATION, "protected-worker-finish-exit")))
                state.operations[str(uuid5(m.LATEST_OPERATION, "finish"))] = "EMERGENCY_FLATTEN"
            elif fault == "ready_marker":
                state.operations[str(uuid5(m.LATEST_OPERATION, "protected-worker-ready"))] = (
                    "FULL_PROTECTED_READY"
                )
            elif fault == "no_finally":
                state.operations.pop(str(uuid5(m.LATEST_OPERATION, "protected-worker-finally")))
            elif fault == "wrong_failure":
                state.last_private_session_failure = PrivateSessionDiagnostic(
                    stage="EVENT", code="TIMEOUT", event_kind="ALGO_UPDATE"
                )
            elif fault == "unverified_stop":
                state.requests["DS-latest"] = "UNKNOWN"
            elif fault == "extra_request":
                state.requests["DX-foreign"] = "NOT_SENT"
            elif fault == "extra_exit_generation":
                state.orders[str(m.LATEST_OPERATION)].exit_generation = 2
            else:
                state.validation_evidence[str(m.LATEST_OPERATION)]["target_verified"] = False

        store.change(lease, "TEST_NEW_INCIDENT_BINDING", change)
        store.release(lease)
    before = store.read("DEMO-test")
    result = m.execute(store, c.client, reference, acknowledged=True, account="DEMO-test")
    assert result["recovery"] == "BLOCKED_KEEP_PAUSED_NO_AUTOMATIC_RETRY", result
    assert result["application_may_have_completed"] is False
    assert store.read("DEMO-test") == before


def test_other_old_operation_identity_not_covered_by_prior_acknowledgement(approved):
    store, c, _, ref = approved
    changed = copy.deepcopy(ref)
    changed["unresolved_requests"][0]["linked_orders"][0]["operation_id"] = str(uuid4())
    before = store.read("DEMO-test")
    report = m.execute(store, c.client, changed, acknowledged=True, account="DEMO-test")
    assert report["recovery"] == "BLOCKED_KEEP_PAUSED_NO_AUTOMATIC_RETRY"
    assert not report["application_may_have_completed"] and store.read("DEMO-test") == before


def test_actual_reference_fields_accept_original_uuid_without_constant_substitution():
    # Independent user-provided projection; do not build this fixture from code constants.
    original = json.loads(Path(__file__).with_name("original-reference-fields.json").read_text())
    m.validate_reference(original)
    bad = copy.deepcopy(original)
    bad["unresolved_requests"][1]["linked_orders"][0]["operation_id"] = (
        "77db044e-11cd-4358-80e6-df9730630213"
    )
    with pytest.raises(m.ScopeMismatch, match="HISTORICAL_NOT_SENT_OPERATION_ID_MISMATCH"):
        m.validate_reference(bad)


def test_scope_rejection_explicitly_reports_no_lease_was_acquired(approved):
    store, c, _, reference = approved
    bad = copy.deepcopy(reference)
    for row in bad["unresolved_requests"]:
        if row["request_kind"] == "DS" and row["local_request_status"] == "NOT_SENT":
            row["linked_orders"][0]["operation_id"] = "77db044e-11cd-4358-80e6-df9730630213"
    before = store.read("DEMO-test")
    result = m.execute(store, c.client, bad, acknowledged=True, account="DEMO-test")
    assert result["stage"] == "OPERATOR_SCOPE"
    assert result["error_code"] == "HISTORICAL_NOT_SENT_OPERATION_ID_MISMATCH"
    assert result["lease_acquired"] is False and result["leader_released"] is None
    assert result["application_may_have_completed"] is False and store.read("DEMO-test") == before
