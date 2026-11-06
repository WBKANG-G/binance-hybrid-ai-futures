"""An old retained canceled order cannot prevent same-week daily evidence."""

from datetime import timedelta
from uuid import uuid4

import pytest

from packages.domain.contracts import RiskState
from services.execution.demo_day_reconstruction import (
    DayReconstructionBlocked,
    apply_day_reconstruction,
    review_day_reconstruction,
)
from services.execution.demo_history_review import HistoryBlocked
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_cancelled_correction import correction  # noqa: F401
from tests.integration.test_demo_day_reconstruction import day_case  # noqa: F401
from tests.unit.test_demo_trade_history_window import row


@pytest.fixture
def long_history(day_case):  # noqa: F811
    store, c, *_ = day_case

    def retain_old_signal(ledger):
        order = next(o for o in ledger.orders.values() if not o.filled)
        order.signal = order.signal.model_copy(
            update={
                "signal_closed_candle_at": order.signal.signal_closed_candle_at - timedelta(days=10)
            }
        )

    store.change(c.lease, "TEST_RETAIN_OLD_CANCELLED_SIGNAL", retain_old_signal)
    return day_case


def preview(case):
    store, c, *_ = case
    return review_day_reconstruction(
        store,
        c.client,
        store.read("DEMO-test"),
        account_id="DEMO-test",
        attest_no_other_trades=True,
    )


def test_old_signal_day_preview_preserves_entire_ledger(long_history):
    store, _, fake, *_ = long_history
    before = store.read("DEMO-test")
    start = len(fake.calls)
    report = preview(long_history)
    assert report["proposed_daily_base_usdt"] == str(fake.wallet)
    assert report["weekly_base_preserved"] and report["high_water_preserved"]
    assert report["source"] == "OPERATOR_ATTESTED_FLAT_WITH_SIGNED_USDT_EVIDENCE"
    assert not report["account_wide_exchange_export_verified"]
    assert not report["database_changed"] and not report["recovery_eligible"]
    assert store.read("DEMO-test") == before
    assert all(m == "GET" for m, _, _ in fake.calls[start:])


def test_old_signal_day_apply_preserves_history_week_highwater_and_lock(long_history):
    store, c, *_ = long_history
    before = store.read("DEMO-test")
    token = preview(long_history)["review_token"]
    op = uuid4()
    result = apply_day_reconstruction(
        store,
        c.lease,
        c.client,
        op,
        token,
        attest_no_other_trades=True,
        accept_operator_evidence=True,
    )
    assert result == "OPERATOR_ATTESTED_DAILY_BASELINE_ADOPTED_KEEP_LOCK"
    after = store.read("DEMO-test")
    assert after.orders == before.orders and after.requests == before.requests
    assert after.weekly_base == before.weekly_base and after.weekly_key == before.weekly_key
    assert after.high_water == before.high_water and after.value == before.value
    assert after.risk_state == RiskState.EMERGENCY and after.paused
    assert after.daily_key == "2026-10-21"
    assert (
        apply_day_reconstruction(
            store,
            c.lease,
            c.client,
            op,
            token,
            attest_no_other_trades=True,
            accept_operator_evidence=True,
        )
        == "ALREADY_RECORDED_NO_ACTION"
    )


def test_invalid_trade_in_oldest_day_blocks_without_writes(long_history):
    store, _, _, rows, *_ = long_history
    before = store.read("DEMO-test")
    oldest = min(o.signal.signal_closed_candle_at for o in before.orders.values())
    rows.append(row(time=int(oldest.timestamp() * 1000), symbol="ETHUSDT"))
    with pytest.raises(HistoryBlocked, match="INVALID_TRADE_IDENTITY"):
        preview(long_history)
    assert store.read("DEMO-test") == before


def test_post_exit_trade_cannot_hide_in_long_history(long_history):
    store, _, _, rows, *_ = long_history
    before = store.read("DEMO-test")
    rows.append(row(id=999, orderId=999, time=int(before.last_reconciled.timestamp() * 1000) + 1))
    with pytest.raises(DayReconstructionBlocked, match="BTC_EXIT_AND_HISTORY_NOT_CONFIRMED"):
        preview(long_history)
    assert store.read("DEMO-test") == before


def test_long_history_time_budget_blocks_apply_and_rolls_back(long_history, monkeypatch):
    import services.execution.demo_recovery_budget as budget

    store, c, *_ = long_history
    token = preview(long_history)["review_token"]
    before = store.read("DEMO-test")
    ticks = iter([0, 41])
    monkeypatch.setattr(budget.time, "monotonic", lambda: next(ticks, 41))
    with pytest.raises(HistoryBlocked, match="REQUEST_AUDIT_TIME_BUDGET"):
        apply_day_reconstruction(
            store,
            c.lease,
            c.client,
            uuid4(),
            token,
            attest_no_other_trades=True,
            accept_operator_evidence=True,
        )
    assert store.read("DEMO-test") == before
