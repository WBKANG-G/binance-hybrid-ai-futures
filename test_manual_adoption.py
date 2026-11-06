"""Real PostgreSQL, synthetic venue, narrowly scoped manual-close adoption."""

import importlib.util
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path
from urllib.parse import parse_qs
from uuid import UUID, uuid4, uuid5

import pytest
from sqlalchemy import select

from integrations.binance_usdm.demo import DemoFailure
from packages.domain.contracts import OrderState, PositionState, RiskState
from packages.persistence.demo import accounts, events
from services.execution.models import PaperOrder
from services.risk.paper import period_keys
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_isolated import configure
from tests.integration.test_paper import signal, snapshot

spec = importlib.util.spec_from_file_location(
    "manual_adopt", Path(__file__).with_name("adopt_manual_close.py")
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


@pytest.fixture
def scenario(demo_system, monkeypatch):  # noqa: F811
    (store, c, fake), _ = configure(demo_system)
    monkeypatch.setattr(m, "ACCOUNT", "DEMO-test")
    sig = signal(
        signal_id=UUID(m.INCIDENT),
        environment="DEMO",
        purpose="DEMO_VALIDATION",
        strategy=None,
        suggested_limit_price="75725.10",
        initial_stop_price="75344.40",
        target_price="76482.40",
        signal_closed_candle_at=datetime(2026, 11, 6, 6, 47, 59, 999000, tzinfo=UTC),
        expires_at=datetime(2026, 11, 6, 6, 48, 10, tzinfo=UTC),
    )
    order = PaperOrder(
        client_id="DE-scoped-synthetic",
        signal=sig,
        snapshot=snapshot(),
        group="BTC",
        quantity=m.QUANTITY,
        quantity_step="0.0001",
        price_tick="0.1",
        loss_per_unit="400",
        budget="1",
        state=OrderState.FILLED,
        position_state=PositionState.OPEN_UNPROTECTED,
        filled=m.QUANTITY,
        remaining="0",
        open_quantity=m.QUANTITY,
        average_price="75725.10",
        actual_risk="0.3",
        algo_id="DS-scoped-synthetic",
        tp_algo_id="DT-scoped-synthetic",
        server_order_id=m.ENTRY,
    )
    entry_time = m.EXPECTED_TRADES["554837511"][-1]
    close_time = m.EXPECTED_TRADES["554838531"][-1]
    fake.orders[order.client_id] = dict(
        symbol="BTCUSDT",
        clientOrderId=order.client_id,
        orderId=m.ENTRY,
        side="BUY",
        type="LIMIT",
        status="FILLED",
        origQty="0.0007",
        executedQty="0.0007",
        avgPrice="75725.10",
        price="75725.10",
        positionSide="BOTH",
        reduceOnly=False,
        time=entry_time - 100,
        updateTime=entry_time,
    )
    fake.orders["web_synthetic_close"] = dict(
        fake.orders[order.client_id],
        clientOrderId="web_synthetic_close",
        orderId=m.CLOSE,
        side="SELL",
        type="MARKET",
        reduceOnly=True,
        avgPrice="75685.20",
        price="0",
        time=close_time,
        updateTime=close_time,
    )
    for cid, kind, price in (
        (order.algo_id, "STOP_MARKET", "75344.40"),
        (order.tp_algo_id, "TAKE_PROFIT_MARKET", "76482.40"),
    ):
        fake.algos[cid] = dict(
            symbol="BTCUSDT",
            clientAlgoId=cid,
            algoId=cid[-5:],
            side="SELL",
            orderType=kind,
            algoStatus="CANCELED",
            triggerPrice=price,
            closePosition=True,
            positionSide="BOTH",
            workingType="MARK_PRICE",
            priceProtect=False,
            algoType="CONDITIONAL",
            actualOrderId="",
        )
    trades = json.loads(Path(__file__).with_name("VENUE_TRADES.json").read_text())
    original = c.client.send

    def send(method, path, query, key):
        if path == "/fapi/v1/userTrades":
            fake.calls.append((method, path, parse_qs(query.get_secret_value())))
            return fake.response(trades)
        return original(method, path, query, key)

    c.client.send = send
    c.client.settings = c.client.settings.model_copy(update={"demo_trading_enabled": False})
    fake.wallet -= D("0.07032487")

    def setup(s):
        s.daily_key, s.weekly_key = period_keys(fake.at, s.policy.week_start_kst)
        s.paused = True
        s.risk_state = RiskState.NORMAL
        s.orders[m.INCIDENT] = order
        s.positions = {"BTCUSDT": m.QUANTITY}
        s.requests.update(
            {
                order.client_id: "ACKNOWLEDGED",
                order.algo_id: "VERIFIED",
                order.tp_algo_id: "VERIFIED",
                "DS-old-ambiguous": "UNKNOWN",
            }
        )
        s.validation_entry_styles[m.INCIDENT] = "OPPOSITE_QUOTE"
        s.operations[str(uuid5(UUID(m.INCIDENT), "tp-trigger-observation"))] = (
            "TP_TRIGGER:NOT_ACCEPTED"
        )
        s.validation_evidence[m.INCIDENT] = {"stop_verified": True, "target_verified": True}
        s.reconciliation_failure_code = "OTHER_DEMO_FAILURE"

    store.release(c.lease)
    fake.at = datetime(2026, 11, 6, 7, tzinfo=UTC)
    fixture_lease = store.acquire("DEMO-test", uuid4())
    store.change(fixture_lease, "TEST_RATE_LIMITED_OPEN_LOCAL_AFTER_MANUAL_CLOSE", setup)
    store.release(fixture_lease)
    return store, c.client, fake, trades


def rows(store):
    with store.engine.connect() as conn:
        return conn.execute(select(accounts)).mappings().all(), conn.execute(
            select(events)
        ).mappings().all()


def test_review_does_not_write_and_apply_preserves_provenance_then_is_idempotent(scenario):
    store, client, fake, _ = scenario
    before, calls = store.read(m.ACCOUNT), len(fake.calls)
    original_rows = rows(store)
    report = m.adopt(store, client)
    assert report["manual_close_proven"] and rows(store) == original_rows
    report = m.adopt(store, client, apply=True)
    assert report["manual_close_adoption"] == "SIGNED_EXTERNAL_CLOSE_ADOPTED_ENTRIES_STILL_PAUSED"
    after = store.read(m.ACCOUNT)
    assert after.orders[m.INCIDENT].position_state == PositionState.CLOSED
    assert after.orders[m.INCIDENT].open_quantity == after.orders[m.INCIDENT].actual_risk == 0
    assert (
        after.orders[m.INCIDENT].filled == m.QUANTITY
        and after.orders[m.INCIDENT].state == OrderState.FILLED
    )
    assert (
        after.orders[m.INCIDENT].exit_id is None and after.orders[m.INCIDENT].exit_state == "NONE"
    )
    assert (
        after.requests == before.requests
        and after.validation_evidence == before.validation_evidence
    )
    assert (
        after.reconciliation_failure_code == "OTHER_DEMO_FAILURE"
        and not after.reconciled
        and not after.stream_ready
    )
    assert (
        after.daily_base == before.daily_base
        and after.weekly_base == before.weekly_base
        and after.high_water == before.high_water
    )
    assert after.paused and after.risk_state == before.risk_state
    assert after.value.equity == fake.wallet
    from services.execution.demo_empty_db_restart import empty

    empty(after)
    receipt = json.loads(after.operations[m.RECEIPT])
    assert receipt["original_order"] == before.orders[m.INCIDENT].model_dump(mode="json")
    assert (
        receipt["manual_close_order_id"] == m.CLOSE
        and receipt["source"] == "SIGNED_OPERATOR_MANUAL_CLOSE"
    )
    assert all(method == "GET" for method, _, _ in fake.calls[calls:])
    stable, call_count = rows(store), len(fake.calls)
    assert (
        m.adopt(store, client, apply=True)["manual_close_adoption"] == "ALREADY_RECORDED_NO_ACTION"
    )
    assert rows(store) == stable and len(fake.calls) == call_count


@pytest.mark.parametrize(
    "fault",
    [
        "wrong_close_id",
        "not_reduce",
        "ack_only",
        "wrong_side",
        "wrong_qty",
        "wrong_price",
        "wrong_client",
        "trade_extra",
        "trade_duplicate",
        "trade_fee",
        "trade_asset",
        "trade_side",
        "trade_time",
        "sl_missing",
        "tp_active",
        "position",
        "loss",
        "high_water",
        "settings",
        "transfer",
        "period",
        "emergency",
        "unpaused",
        "other_exposure",
        "write_enabled",
        "snapshot",
        "late_query_failure",
        "budget",
    ],
)
def test_unsafe_or_ambiguous_proof_cannot_mutate_ledger(scenario, monkeypatch, fault):
    store, client, fake, trades = scenario
    state = store.read(m.ACCOUNT)
    closing = fake.orders["web_synthetic_close"]
    fields = {
        "wrong_close_id": ("orderId", "bad"),
        "not_reduce": ("reduceOnly", False),
        "ack_only": ("status", "NEW"),
        "wrong_side": ("side", "BUY"),
        "wrong_qty": ("executedQty", "0.0006"),
        "wrong_price": ("avgPrice", "1"),
        "wrong_client": ("clientOrderId", "DX-fake"),
    }
    if fault in fields:
        key, value = fields[fault]
        closing[key] = value
    elif fault == "trade_extra":
        trades.append(dict(trades[0], id="extra"))
    elif fault == "trade_duplicate":
        trades[1] = trades[0].copy()
    elif fault.startswith("trade_"):
        key, value = {
            "trade_fee": ("commission", "0"),
            "trade_asset": ("commissionAsset", "BTC"),
            "trade_side": ("side", "BUY"),
            "trade_time": ("time", 1),
        }[fault]
        trades[1][key] = value
    elif fault == "sl_missing":
        fake.algos.pop(state.orders[m.INCIDENT].algo_id)
    elif fault == "tp_active":
        fake.algos[state.orders[m.INCIDENT].tp_algo_id]["algoStatus"] = "NEW"
    elif fault == "position":
        fake.positions["BTCUSDT"] = m.QUANTITY
    elif fault == "loss":
        fake.wallet = D("1")
    elif fault == "high_water":
        fake.wallet = D("10001")
    elif fault == "settings":
        fake.leverage = "1"
    elif fault == "transfer":
        state.income_seen["new"] = "1"
    elif fault == "period":
        fake.at += timedelta(days=1)
    elif fault == "emergency":
        state.risk_state = RiskState.EMERGENCY
    elif fault == "unpaused":
        state.paused = False
    elif fault == "other_exposure":
        state.positions["ETHUSDT"] = D("1")
    elif fault == "write_enabled":
        client.settings = client.settings.model_copy(update={"demo_trading_enabled": True})
    elif fault == "budget":
        monkeypatch.setattr(
            m.Budget, "signed", lambda self: (_ for _ in ()).throw(m.Blocked("BUDGET_TEST"))
        )
    elif fault in {"snapshot", "late_query_failure"}:
        original, count = m.operational_snapshot, []

        def changed(*args):
            snap = original(*args)
            count.append(1)
            if len(count) == 2:
                if fault == "late_query_failure":
                    raise DemoFailure("PRIVATE_RAW_DATA")
                snap.fingerprint = "changed"
            return snap

        monkeypatch.setattr(m, "operational_snapshot", changed)
    before, start, local = rows(store), len(fake.calls), state.model_dump_json()
    with pytest.raises((ValueError, DemoFailure)):
        m.prove(store, client, state)
    assert rows(store) == before and state.model_dump_json() == local
    assert all(method == "GET" for method, _, _ in fake.calls[start:])


def test_live_owner_and_fenced_apply_block(scenario, monkeypatch):
    store, client, _, _ = scenario
    from uuid import uuid4

    lease = store.acquire(m.ACCOUNT, uuid4())
    before = rows(store)
    with pytest.raises(m.Blocked, match="ACTIVE_OWNER"):
        m.adopt(store, client, apply=True)
    assert rows(store) == before
    store.release(lease)
    original = m.prove

    def fenced(*args):
        result = original(*args)
        with store.engine.begin() as conn:
            conn.execute(
                accounts.update()
                .where(accounts.c.account_id == m.ACCOUNT)
                .values(fence=accounts.c.fence + 1)
            )
        return result

    monkeypatch.setattr(m, "prove", fenced)
    with pytest.raises(m.Blocked, match="RELEASE_UNVERIFIED"):
        m.adopt(store, client, apply=True)
    assert store.read(m.ACCOUNT).orders[m.INCIDENT].open_quantity == m.QUANTITY
    assert m.RECEIPT not in store.read(m.ACCOUNT).operations


@pytest.mark.parametrize("fault", ["stale", "account_change", "transaction_abort", "rate_limit"])
def test_apply_failure_never_commits_partial_adoption(scenario, monkeypatch, fault):
    store, client, fake, _ = scenario
    before = store.read(m.ACCOUNT)
    if fault == "rate_limit":
        fake.rate_limit = True
    elif fault == "transaction_abort":
        original = store.change

        def change(lease, kind, callback):
            if kind == "DEMO_SIGNED_OPERATOR_MANUAL_CLOSE_ADOPTED":

                def abort(ledger):
                    callback(ledger)
                    raise RuntimeError("SYNTHETIC_PRIVATE_DATABASE_FAILURE")

                return original(lease, kind, abort)
            return original(lease, kind, callback)

        monkeypatch.setattr(store, "change", change)
    else:
        original = m.prove

        def changed(*args):
            result = original(*args)
            if fault == "stale":
                fake.at += timedelta(seconds=before.policy.max_snapshot_age_seconds + 1)
            else:
                with store.engine.begin() as conn:
                    raw = store.read(m.ACCOUNT).model_dump(mode="json")
                    raw["operations"]["external-change"] = "TEST"
                    conn.execute(
                        accounts.update()
                        .where(accounts.c.account_id == m.ACCOUNT)
                        .values(state=raw)
                    )
            return result

        monkeypatch.setattr(m, "prove", changed)
    with pytest.raises((m.Blocked, DemoFailure, RuntimeError)):
        m.adopt(store, client, apply=True)
    after = store.read(m.ACCOUNT)
    assert after.orders == before.orders and after.value == before.value
    assert after.requests == before.requests and after.positions == before.positions
    assert m.RECEIPT not in after.operations
    assert m.read_local(store)[0] is None


def test_real_budget_limits():
    clock = [0.0]
    b = m.Budget(clock=lambda: clock[0])
    for _ in range(64):
        b.signed()
    with pytest.raises(m.Blocked, match="QUERY_BUDGET"):
        b.signed()
    for _ in range(3):
        b.probe()
    with pytest.raises(m.Blocked, match="CLOCK_BUDGET"):
        b.probe()
    clock[0] = 20
    with pytest.raises(m.Blocked, match="TIME_BUDGET"):
        b.check()


def test_main_redacts_private_failure(monkeypatch, capsys):
    import sys

    monkeypatch.setattr(sys, "argv", ["adopt"])

    def fail(**kwargs):
        raise DemoFailure("PRIVATE_KEY_AND_SIGNED_QUERY")

    monkeypatch.setattr(m, "credentials", fail)
    with pytest.raises(SystemExit) as code:
        m.main()
    output = capsys.readouterr().out
    assert code.value.code == 2 and "PRIVATE_KEY_AND_SIGNED_QUERY" not in output
    assert "BLOCKED_KEEP_PAUSED_NO_AUTOMATIC_RETRY" in output


def test_reference_matches_independently_recorded_user_evidence():
    facts = json.loads(Path(__file__).with_name("INCIDENT_REFERENCE.json").read_text())
    assert (
        facts["incident"] == m.INCIDENT
        and facts["entry_order_id"] == m.ENTRY
        and facts["closing_order_id"] == m.CLOSE
    )
    expected = {
        t["id"]: (
            t["order_id"],
            t["side"],
            t["price"],
            t["realized_pnl"],
            t["commission"],
            t["time"],
        )
        for t in facts["trades"]
    }
    assert expected == m.EXPECTED_TRADES
    assert sum((D(t["realized_pnl"]) - D(t["commission"]) for t in facts["trades"]), D(0)) == D(
        facts["net_usdt"]
    )


def test_installed_runtime_pins_match_reviewed_source():
    import hashlib

    assert len(m.SOURCES) == 16
    for path, sha in m.SOURCES.items():
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == sha


def test_previous_transcription_error_is_rejected_before_apply(scenario, monkeypatch):
    store, client, fake, _ = scenario
    expected = dict(m.EXPECTED_TRADES)
    expected["554838531"] = (
        m.CLOSE,
        "SELL",
        "75685.20",
        "-0.02793000",
        "0.02111985",
        1793947973987,
    )
    monkeypatch.setattr(m, "EXPECTED_TRADES", expected)
    before = store.read(m.ACCOUNT)
    progress = {}
    with pytest.raises(m.TradeMismatch) as blocked:
        m.adopt(store, client, apply=True, progress=progress)
    assert blocked.value.fields == ["commission"]
    assert progress == {"application_may_have_completed": False, "lease_changes_possible": True}
    after = store.read(m.ACCOUNT)
    assert after.orders == before.orders and after.value == before.value
    assert m.RECEIPT not in after.operations and m.read_local(store)[0] is None


def test_net_pnl_is_computed_from_venue_values(scenario):
    store, client, _, _ = scenario
    _, proof = m.prove(store, client, store.read(m.ACCOUNT))
    assert D(proof["net_usdt"]) == D("-0.07032487")
    assert D("75685.20") * D("0.0007") * D("0.0004") // D("0.00000001") * D("0.00000001") == D(
        "0.02119185"
    )


def test_application_uncertainty_remains_true_after_commit_attempt(scenario, monkeypatch):
    store, client, _, _ = scenario
    original = store.change

    def change(lease, kind, callback):
        result = original(lease, kind, callback)
        if kind == "DEMO_SIGNED_OPERATOR_MANUAL_CLOSE_ADOPTED":
            raise RuntimeError("SYNTHETIC_COMMIT_ACK_LOST")
        return result

    monkeypatch.setattr(store, "change", change)
    progress = {}
    with pytest.raises(RuntimeError):
        m.adopt(store, client, apply=True, progress=progress)
    assert progress["application_may_have_completed"]
    assert m.RECEIPT in store.read(m.ACCOUNT).operations
    assert m.read_local(store)[0] is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("symbol", "ETHUSDT"),
        ("orderId", 1),
        ("positionSide", "LONG"),
        ("qty", "0.0006"),
        ("price", "1"),
        ("realizedPnl", "0"),
    ],
)
def test_field_diagnostics_reject_each_wrong_identity_or_amount(scenario, field, value):
    store, client, _, trades = scenario
    trades[1][field] = value
    before = rows(store)
    with pytest.raises(m.TradeMismatch) as error:
        m.prove(store, client, store.read(m.ACCOUNT))
    assert error.value.fields == [field]
    assert rows(store) == before
