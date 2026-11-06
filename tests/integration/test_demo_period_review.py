"""Fenced manual day/week reconstruction with real PG and synthetic signed truth."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import parse_qs
from uuid import uuid4

import pytest
from sqlalchemy import select

from integrations.binance_usdm.demo import DemoFailure
from packages.domain.contracts import PositionState, RiskState
from packages.persistence.demo import events
from services.execution.demo_period_review import (
    SOURCE,
    PeriodReviewBlocked,
    apply_periods,
    review_periods,
)
from services.risk.paper import RiskRejected
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_cancelled_correction import correction, event_count  # noqa: F401
from tests.integration.test_demo_day_reconstruction import day_case  # noqa: F401
from tests.unit.test_demo_trade_history_window import row


@pytest.fixture
def period_case(day_case):  # noqa: F811
    store, c, fake, rows, incomes, raw_updates = day_case
    store.release(c.lease)
    fake.at = datetime(2026, 10, 24, 3, 3, 44, 968408, tzinfo=UTC)
    c.lease = store.acquire("DEMO-test", uuid4())
    fake.wallet = Decimal("4999.64804199")

    def bind(ledger):
        ledger.value = ledger.value.model_copy(
            update={"as_of": fake.at, "initial": Decimal(5000), "realized": fake.wallet - 5000}
        )
        ledger.last_reconciled = fake.at
        ledger.daily_key = "2026-10-24"
        ledger.weekly_key = "2026-10-19"
        ledger.daily_base = Decimal("4999.74584261")
        ledger.weekly_base = ledger.high_water = Decimal(5000)

    store.change(c.lease, "TEST_VERIFIED_FLAT_PRE_WEEK_ANCHOR", bind)
    store.release(c.lease)
    fake.at = datetime(2026, 10, 30, 4, tzinfo=UTC)
    c.lease = store.acquire("DEMO-test", uuid4())
    original = c.client.send

    def audited(method, path, query, key):
        if path in {"/fapi/v1/income", "/fapi/v1/userTrades"}:
            params = {
                k: v[0]
                for k, v in parse_qs(query.get_secret_value()).items()
                if k not in {"signature", "timestamp", "recvWindow"}
            }
            fake.calls.append((method, path, params))
        return original(method, path, query, key)

    c.client.send = audited
    c.client.sync_clock()
    return day_case


def review(case, attest=True, state=None):
    store, c, *_ = case
    return review_periods(
        store,
        c.client,
        state or store.read("DEMO-test"),
        account_id="DEMO-test",
        attest_no_activity=attest,
    )


def apply(case, token, op=None, attest=True, accept=True, lease=None):
    store, c, *_ = case
    return apply_periods(
        store,
        lease or c.lease,
        c.client,
        op or uuid4(),
        token,
        attest_no_activity=attest,
        accept_operator_evidence=accept,
    )


def test_preview_matches_six_day_idle_gap_without_writes(period_case):
    store, c, fake, *_ = period_case
    before = store.read("DEMO-test")
    count = event_count(store)
    start = len(fake.calls)
    report = review(period_case)
    assert report["source"] == SOURCE
    assert report["proposed_day_kst"] == "2026-10-30"
    assert report["proposed_week_kst"] == "2026-10-26"
    assert report["week_boundary_utc"] == "2026-10-25T15:00:00+00:00"
    assert report["day_boundary_utc"] == "2026-10-29T15:00:00+00:00"
    assert (
        report["proposed_daily_base_usdt"] == report["proposed_weekly_base_usdt"] == "4999.64804199"
    )
    assert report["account_wide_exchange_export_verified"] is False
    assert report["operator_attestation_used"] is True
    assert report["recovery_eligible"] is False
    assert store.read("DEMO-test") == before and event_count(store) == count
    assert all(m == "GET" and "asyn" not in p for m, p, _ in fake.calls[start:])
    # Both signed history reads use the flat anchor, not the earliest old order.
    starts = [
        p["startTime"]
        for m, path, p in fake.calls[start:]
        if path == "/fapi/v1/income" and "startTime" in p
    ]
    assert str(int(before.last_reconciled.timestamp() * 1000)) in starts


def test_apply_only_changes_two_periods_and_audit_preserves_lock_timestamps_requests(period_case):
    store, c, fake, *_ = period_case
    before = store.read("DEMO-test")
    token = review(period_case)["review_token"]
    op = uuid4()
    assert apply(period_case, token, op) == "OPERATOR_ATTESTED_PERIOD_BASELINES_ADOPTED_KEEP_LOCK"
    after = store.read("DEMO-test")
    excluded = {
        "daily_key",
        "daily_base",
        "weekly_key",
        "weekly_base",
        "attested_period_baselines",
        "operations",
        "reconciled",
        "stream_ready",
    }
    assert before.model_dump(exclude=excluded) == after.model_dump(exclude=excluded)
    assert after.daily_key == "2026-10-30" and after.weekly_key == "2026-10-26"
    assert after.daily_base == after.weekly_base == fake.wallet
    assert after.high_water == 5000 and after.paused and after.risk_state == RiskState.EMERGENCY
    assert after.requests["DX-old-ambiguous"] == "SUBMITTING"
    assert after.requests["DS-old-ambiguous"] == "UNKNOWN"
    receipt = after.attested_period_baselines[str(op)]
    assert receipt.old_weekly_key == "2026-10-19" and receipt.old_weekly_base == 5000
    assert receipt.old_daily_key == "2026-10-24" and receipt.old_daily_base == before.daily_base
    assert (
        receipt.anchor_utc == before.last_reconciled
        and not receipt.account_wide_exchange_export_verified
    )
    with store.engine.connect() as conn:
        payload = conn.execute(
            select(events.c.payload).where(
                events.c.kind == "DEMO_OPERATOR_ATTESTED_PERIOD_BASELINES"
            )
        ).scalar_one()
    assert token in payload and SOURCE in payload
    calls = len(fake.calls)
    assert apply(period_case, token, op) == "ALREADY_RECORDED_NO_ACTION"
    assert len(fake.calls) == calls and store.read("DEMO-test") == after


@pytest.mark.parametrize("attest,accept", [(False, True), (True, False), (False, False)])
def test_apply_needs_attestation_and_manual_source_acceptance(period_case, attest, accept):
    before = period_case[0].read("DEMO-test")
    start = len(period_case[2].calls)
    with pytest.raises(PeriodReviewBlocked, match="MANUAL_SOURCE_ACCEPTANCE"):
        apply(period_case, "0" * 64, attest=attest, accept=accept)
    assert period_case[0].read("DEMO-test") == before and len(period_case[2].calls) == start


def test_no_attestation_no_network(period_case):
    start = len(period_case[2].calls)
    with pytest.raises(PeriodReviewBlocked, match="ATTESTATION_REQUIRED"):
        review(period_case, False)
    assert len(period_case[2].calls) == start


@pytest.mark.parametrize(
    "change",
    [
        "live_client",
        "wrong_scope",
        "crossed",
        "normal",
        "unpaused",
        "leverage",
        "missing_anchor",
        "valuation_time",
        "positions",
        "open_quantity",
        "remaining",
        "risk",
        "protected",
        "not_closed",
        "entry_pending",
        "net_transfer",
        "deposit",
        "income_seen",
        "tracking_missing",
        "tracking_late",
        "anchor_after_week",
        "too_old",
        "stored_day",
        "stored_week",
    ],
)
def test_invalid_context_or_anchor_cannot_reconstruct(period_case, change):
    store, c, fake, *_ = period_case
    ledger = store.read("DEMO-test")
    order = next(iter(ledger.orders.values()))
    if change == "live_client":
        c.client.settings = c.client.settings.model_copy(update={"demo_trading_enabled": True})
    elif change == "wrong_scope":
        ledger.symbols = ("ETHUSDT",)
    elif change == "crossed":
        ledger.margin_mode = "CROSSED"
    elif change == "normal":
        ledger.risk_state = RiskState.NORMAL
    elif change == "unpaused":
        ledger.paused = False
    elif change == "leverage":
        ledger.verified_leverage["BTCUSDT"] = Decimal(3)
    elif change == "missing_anchor":
        ledger.last_reconciled = None
    elif change == "valuation_time":
        ledger.value = ledger.value.model_copy(
            update={"as_of": ledger.value.as_of + timedelta(seconds=1)}
        )
    elif change == "positions":
        ledger.positions["BTCUSDT"] = Decimal(".01")
    elif change == "open_quantity":
        order.open_quantity = Decimal(".01")
    elif change == "remaining":
        order.remaining = Decimal(".01")
    elif change == "risk":
        order.actual_risk = Decimal(1)
    elif change == "protected":
        order.protected = True
    elif change == "not_closed":
        order.position_state = PositionState.EXIT_PENDING
    elif change == "entry_pending":
        order.state = "ACKNOWLEDGED"
    elif change == "net_transfer":
        ledger.net_transfers = Decimal(1)
    elif change == "deposit":
        ledger.value = ledger.value.model_copy(update={"net_deposits": Decimal(1)})
    elif change == "income_seen":
        ledger.income_seen["TRANSFER:1"] = "0"
    elif change == "tracking_missing":
        ledger.income_since_ms = None
    elif change == "tracking_late":
        ledger.income_since_ms = int(ledger.last_reconciled.timestamp() * 1000) + 1
    elif change == "anchor_after_week":
        ledger.last_reconciled = datetime(2026, 10, 27, tzinfo=UTC)
        ledger.value = ledger.value.model_copy(update={"as_of": ledger.last_reconciled})
    elif change == "too_old":
        fake.at += timedelta(days=2)
    elif change == "stored_day":
        ledger.daily_key = "2026-10-23"
    elif change == "stored_week":
        ledger.weekly_key = "2026-10-12"
    start = len(fake.calls)
    with pytest.raises(PeriodReviewBlocked):
        review(period_case, state=ledger)
    assert len(fake.calls) == start


@pytest.mark.parametrize(
    "kind",
    [
        "trade",
        "commission",
        "transfer",
        "zero_income",
        "non_usdt",
        "wallet",
        "unrealized",
        "no_usdt",
        "duplicate_usdt",
        "total",
        "remote_position",
        "active_order",
        "active_algo",
        "exit_qty",
        "exit_not_found",
        "entry_side",
        "remote_leverage",
        "changed_second_history",
    ],
)
def test_signed_contradiction_beats_operator_attestation(period_case, kind):
    store, c, fake, rows, incomes, updates = period_case
    before = store.read("DEMO-test")
    stamp = int(before.last_reconciled.timestamp() * 1000) + 1000
    filled = next(o for o in before.orders.values() if o.filled)
    if kind == "trade":
        rows.append(row(id=99, time=stamp, orderId=999))
    elif kind in {"commission", "transfer", "zero_income", "non_usdt"}:
        incomes.append(
            {
                "incomeType": "TRANSFER" if kind == "transfer" else "COMMISSION",
                "tranId": 99,
                "time": stamp,
                "income": "0" if kind == "zero_income" else "-1",
                "asset": "BTC" if kind == "non_usdt" else "USDT",
            }
        )
    elif kind == "wallet":
        fake.wallet -= 1
    elif kind == "unrealized":
        updates["totalUnrealizedProfit"] = "1"
    elif kind == "no_usdt":
        updates["assets"] = []
    elif kind == "duplicate_usdt":
        updates["assets"] = [{"asset": "USDT"}, {"asset": "USDT"}]
    elif kind == "total":
        updates["totalMarginBalance"] = "5000"
    elif kind == "remote_position":
        fake.positions["BTCUSDT"] = Decimal(".001")
    elif kind == "active_order":
        fake.orders[filled.client_id]["status"] = "NEW"
    elif kind == "active_algo":
        next(iter(fake.algos.values()))["algoStatus"] = "NEW"
    elif kind == "exit_qty":
        fake.orders[filled.exit_id]["executedQty"] = "0"
    elif kind == "exit_not_found":
        fake.orders.pop(filled.exit_id)
    elif kind == "entry_side":
        fake.orders[filled.client_id]["side"] = "SELL"
    elif kind == "remote_leverage":
        fake.leverage = "3"
    elif kind == "changed_second_history":
        original = c.client.send
        calls = 0

        def send(method, path, query, key):
            nonlocal calls
            if path == "/fapi/v3/account":
                calls += 1
                if calls == 2:
                    incomes.append(
                        {
                            "incomeType": "COMMISSION",
                            "tranId": 99,
                            "time": stamp,
                            "income": "0",
                            "asset": "USDT",
                        }
                    )
            return original(method, path, query, key)

        c.client.send = send
    with pytest.raises(DemoFailure):
        review(period_case)
    assert store.read("DEMO-test") == before


@pytest.mark.parametrize("field", ["daily_base", "weekly_base", "high_water"])
def test_old_loss_limits_cannot_be_erased_by_new_baselines(period_case, field):
    ledger = period_case[0].read("DEMO-test")
    setattr(ledger, field, Decimal(10000))
    with pytest.raises(PeriodReviewBlocked, match="EXISTING_LOSS_LIMIT"):
        review(period_case, state=ledger)


def test_low_high_water_requires_separate_review(period_case):
    ledger = period_case[0].read("DEMO-test")
    ledger.high_water = Decimal(4999)
    with pytest.raises(PeriodReviewBlocked, match="HIGH_WATER"):
        review(period_case, state=ledger)


def test_token_conflict_and_stale_fence_roll_back(period_case):
    store, c, fake, *_ = period_case
    before = store.read("DEMO-test")
    token = review(period_case)["review_token"]
    with pytest.raises(PeriodReviewBlocked, match="PREVIEW_CHANGED"):
        apply(period_case, "0" * 64)
    assert store.read("DEMO-test") == before
    op = uuid4()
    store.change(
        c.lease, "TEST_CONFLICT", lambda ledger: ledger.operations.__setitem__(str(op), "OTHER")
    )
    with pytest.raises(PeriodReviewBlocked, match="OPERATION_CONFLICT"):
        apply(period_case, token, op)
    old = c.lease
    store.release(old)
    c.lease = store.acquire("DEMO-test", uuid4())
    with pytest.raises(RiskRejected, match="fenced"):
        apply(period_case, token, lease=old)


def test_new_trade_after_preview_blocks_apply_without_baseline_changes(period_case):
    store, c, fake, rows, *_ = period_case
    token = review(period_case)["review_token"]
    before = store.read("DEMO-test")
    rows.append(row(id=99, time=int(before.last_reconciled.timestamp() * 1000) + 1, orderId=999))
    with pytest.raises(PeriodReviewBlocked, match="HISTORY_NOT_EMPTY"):
        apply(period_case, token)
    assert store.read("DEMO-test") == before


def test_cli_preview_exposes_manual_source_without_lease_or_database_write(
    period_case, monkeypatch, capsys
):
    import os
    import sys

    import scripts.review_demo_periods as cli

    store, c, *_ = period_case
    original = store.read
    before = original("DEMO-test").model_dump_json()
    count = event_count(store)
    monkeypatch.setattr(cli, "ACCOUNT_ID", "DEMO-test")
    monkeypatch.setattr(sys, "argv", ["review_demo_periods", "--attest-no-activity-since-anchor"])
    monkeypatch.setattr(cli, "create_engine", lambda *a, **k: store.engine)
    monkeypatch.setattr(cli, "DemoStore", lambda engine: store)
    monkeypatch.setattr(cli, "DemoClient", lambda *a, **k: c.client)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "SYNTHETIC_PRIVATE_KEY")

    def no_write(*args):
        raise AssertionError("Preview must not acquire, change or release")

    monkeypatch.setattr(store, "acquire", no_write)
    monkeypatch.setattr(store, "change", no_write)
    monkeypatch.setattr(store, "release", no_write)
    cli.main()
    output = capsys.readouterr().out
    assert SOURCE in output and "OPERATOR_ATTESTED_PERIOD_PREVIEW_NO_DATABASE_CHANGE" in output
    assert '"account_wide_exchange_export_verified": false' in output
    assert "SYNTHETIC_PRIVATE_KEY" not in output
    assert "BINANCE_DEMO_API_KEY" not in os.environ and "BINANCE_DEMO_API_SECRET" not in os.environ
    assert original("DEMO-test").model_dump_json() == before and event_count(store) == count


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        [
            "--attest-no-activity-since-anchor",
            "--apply-review",
            "f" * 64,
            "--operation-id",
            "8fae5b75-99a8-40a8-9371-50f962728dc8",
        ],
        ["--attest-no-activity-since-anchor", "--accept-operator-evidence"],
    ],
)
def test_cli_requires_attestation_and_separate_acceptance_before_apply(
    monkeypatch, capsys, arguments
):
    import sys

    import scripts.review_demo_periods as cli

    monkeypatch.setattr(sys, "argv", ["review_demo_periods", *arguments])

    def no_prompt(*args):
        raise AssertionError("Invalid flags must not ask keys")

    monkeypatch.setattr(cli.getpass, "getpass", no_prompt)
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    assert "BLOCKED_KEEP_LOCK" in capsys.readouterr().out


def test_cli_apply_requires_acceptance_and_preserves_emergency(period_case, monkeypatch, capsys):
    import sys

    import scripts.review_demo_periods as cli

    store, c, *_ = period_case
    token = review(period_case)["review_token"]
    op = uuid4()
    original_read, original_acquire = store.read, store.acquire
    before = original_read("DEMO-test")
    store.release(c.lease)
    monkeypatch.setattr(cli, "ACCOUNT_ID", "DEMO-test")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "review_demo_periods",
            "--attest-no-activity-since-anchor",
            "--accept-operator-evidence",
            "--apply-review",
            token,
            "--operation-id",
            str(op),
        ],
    )
    monkeypatch.setattr(cli, "create_engine", lambda *a, **k: store.engine)
    monkeypatch.setattr(cli, "DemoStore", lambda engine: store)
    monkeypatch.setattr(cli, "DemoClient", lambda *a, **k: c.client)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "SYNTHETIC_PRIVATE_KEY")
    monkeypatch.setattr(store, "acquire", lambda account, owner: original_acquire(account, owner))
    cli.main()
    output = capsys.readouterr().out
    assert "OPERATOR_ATTESTED_PERIOD_BASELINES_ADOPTED_KEEP_LOCK" in output and SOURCE in output
    assert "SYNTHETIC_PRIVATE_KEY" not in output
    after = original_read("DEMO-test")
    assert after.value == before.value and after.requests == before.requests
    assert after.paused and after.risk_state.value == "EMERGENCY"
    assert str(op) in after.attested_period_baselines
