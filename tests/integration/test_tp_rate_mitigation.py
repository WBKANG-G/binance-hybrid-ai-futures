"""PostgreSQL/local WSS regression: idle scheduling and rate-limited cleanup."""

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from packages.domain.contracts import RiskState
from services.execution import demo_validation as validation
from services.execution.demo_stream import entry_wait_seconds
from services.execution.demo_tp_trigger_check import PooledReadTPTriggerCoordinator
from tests.integration.test_demo import posts
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_opposite_tp import exercise_opposite, opposite_case  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_tp_trigger_check import case  # noqa: F401


@pytest.mark.parametrize("mode", ["tp", "unfilled", "no_tp", "sl", "bad_qty", "missing_cancel"])
def test_new_pooled_coordinator_preserves_original_entry_brackets_and_cleanup(
    opposite_case,  # noqa: F811
    monkeypatch,
    mode,  # noqa: F811
):
    store, old, fake, options = opposite_case
    c = PooledReadTPTriggerCoordinator(store, old.lease, old.client)
    c.refresh_metadata()
    c.reconcile()
    if mode == "unfilled":
        options.update(fill=False, trigger=False)
    elif mode == "no_tp":
        options["trigger"] = False
    elif mode == "sl":
        options["trigger"] = "SL"
    elif mode == "bad_qty":
        options["tamper"] = True
    elif mode == "missing_cancel":
        options["cancel_query"] = True
    report = exercise_opposite((store, c, fake, options), monkeypatch)
    assert report["entry_post_attempts"] == report["new_entry_post_count"] == 1
    assert posts(fake)[0]["type"] == "LIMIT" and Decimal(posts(fake)[0]["quantity"]) == Decimal(
        "0.0007"
    )
    assert not any(fake.positions.values()) and store.read("DEMO-test").paused
    assert report["history_and_baselines_preserved"]
    assert (report["tp_trigger_check"] == "TP_FILLED_THEN_REMAINING_SL_CANCELED_FLAT_VERIFIED") == (
        mode == "tp"
    )


def test_pooled_idle_reconcile_bound_and_entry_expiry_are_both_preserved(opposite_case):  # noqa: F811
    store, old, _, _ = opposite_case
    c = PooledReadTPTriggerCoordinator(store, old.lease, old.client)
    state = store.read("DEMO-test")
    assert c.idle_wait_seconds(state) == min(15, state.policy.max_snapshot_age_seconds / 2)
    assert entry_wait_seconds(old) == 5
    assert entry_wait_seconds(c) == c.idle_wait_seconds(state)
    # Session uses the exact nearer expiry even when the idle budget is longer.
    ledger = SimpleNamespace(
        orders={
            "pending": SimpleNamespace(
                remaining=1, signal=SimpleNamespace(expires_at=store.now() + timedelta(seconds=0.4))
            )
        }
    )
    wrapper = SimpleNamespace(
        store=SimpleNamespace(read=lambda _: ledger, now=store.now),
        lease=c.lease,
        idle_wait_seconds=lambda _: 15,
    )
    assert entry_wait_seconds(wrapper) == 0.4


@pytest.mark.parametrize(
    "state",
    [RiskState.NORMAL, RiskState.DAILY_LOCK, RiskState.WEEKLY_LOCK, RiskState.DRAWDOWN_LOCK],
)
def test_local_pause_persists_without_clock_and_preserves_hard_locks(opposite_case, state):  # noqa: F811
    store, c, fake, _ = opposite_case

    def mark(ledger):
        ledger.paused = False
        ledger.positions["BTCUSDT"] = Decimal("0.0007")
        ledger.risk_state = state

    store.change(c.lease, "TEST_EXPOSURE", mark)
    c.client.cooldown_until = c.client.clock_ms() + 120000
    c.session_diagnostic.code = "RATE_LIMITED"
    before = len(fake.calls)
    validation.pause_finalization_locally(c)
    after = store.read("DEMO-test")
    assert after.paused and not after.reconciled and not after.stream_ready
    assert after.risk_state == (RiskState.EMERGENCY if state == RiskState.NORMAL else state)
    assert len(fake.calls) == before


def test_real_rate_response_after_protection_never_retries_or_reports_flat(
    opposite_case,  # noqa: F811
    monkeypatch,
    capsys,  # noqa: F811
):
    store, c, fake, options = opposite_case
    options["trigger"] = False
    original = c.client.send
    reached = []

    def rate(method, path, query, key):
        ledger = store.read("DEMO-test")
        protected = any(o.protected and o.tp_algo_id for o in ledger.orders.values())
        if protected and method == "GET" and path == "/fapi/v1/time":
            reached.append(True)
            return type(fake.response({}))(429, b"not-json", {"Retry-After": "120"})
        return original(method, path, query, key)

    c.client.send = rate
    report = exercise_opposite(opposite_case, monkeypatch)
    output = capsys.readouterr().out
    assert (
        reached
        and report["tp_trigger_check"] == "NOT_ACCEPTED"
        and report["flat_confirmation"] == "UNKNOWN"
    )
    assert report["risk_state"] == "EMERGENCY" and report["final_paused"]
    assert report["operator_exit_post_attempts"] == 0 and report["sl_delete_attempts"] == 0
    assert "RATE_LIMITED_VERIFY_EXCHANGE_MANUALLY" in output
    assert any(fake.positions.values())  # Fixture cleanup is not a claim of flat.
    assert c.client.rate_limit_diagnostics()["http_rate_limit_status"] == 429
