"""Pending same-week source review cannot grant original or trading authority."""

import copy
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from services.execution.demo_source_reuse_review import SourceReuseBlocked, review_eligibility

ROOT = Path(__file__).resolve().parents[2]


def fixture(day="2026-11-24"):
    p = json.loads((ROOT / "docs/reviews/20261124/SOURCE_ACCEPTANCE_REVIEW.json").read_text())[
        "proposal"
    ]
    end = int(datetime.fromisoformat(day + "T03:00:00+00:00").timestamp() * 1000)
    account = dict(
        usdt_wallet="4998.74183608",
        usdt_available="4998.74183608",
        usdt_margin_balance="4998.74183608",
        total_initial_margin="0.00000000",
        total_unrealized_profit="0.00000000",
        positions_flat=True,
        flat_unencumbered_usdt=True,
    )
    row = dict(
        regular_orders=dict(count=0, empty=True),
        algo_orders=dict(count=0, empty=True),
        positions=dict(
            flat=True, nonzero_positions=0, nonzero_non_btc_positions=0, hedge_side_rows=0
        ),
        account=account,
    )
    mode = dict(one_way=True, single_asset=True, account_can_trade=True)
    cfg = dict(
        symbol="BTCUSDT", leverage=2, margin_type="ISOLATED", automatic_margin_addition=False
    )
    s = dict(
        issues=[],
        observations_consistent_with_expected_flat_profile=True,
        mode_equal_at_reads=True,
        btc_config_equal_at_reads=True,
        account_equal_at_reads=True,
        mode_before=copy.deepcopy(mode),
        mode_after=copy.deepcopy(mode),
        btc_config_before=copy.deepcopy(cfg),
        btc_config_after=copy.deepcopy(cfg),
        rounds=[copy.deepcopy(row), copy.deepcopy(row)],
        income_gap_rows=0,
        btc_trade_gap_rows=0,
        income_gap_page_limit_reached=False,
        trade_gap_page_limit_reached=False,
    )
    o = dict(
        observations_end_ms=end,
        observed_day_key_kst=day,
        observed_week_key_kst="2026-11-23",
        current_read="CURRENT_ACCOUNT_OBSERVATIONS_SAVED_NO_CHANGES",
        saved_source_hashes_verified_before_after=True,
        open_order_and_current_position_scope="ALL_SYMBOLS",
        btc_configuration_and_trade_history_scope="BTCUSDT",
        summary=s,
        order_writes=0,
    )
    for k in (
        "settings_changed_by_tool",
        "request_states_changed",
        "baseline_adopted",
        "execution_approved",
        "orders_enabled",
        "live_trading_enabled",
        "account_wide_historical_trade_history_verified",
        "boundary_positions_independently_verified",
        "equity_baseline_reconstructed",
    ):
        o[k] = False
    return p, o, dict(verified_reference_start_ms=end - 60000, server_now_ms=end + 1000)


@pytest.mark.parametrize("day", ["2026-11-24", "2026-11-25", "2026-11-29"])
def test_matching_day_is_only_eligible_for_review_and_inputs_are_unchanged(day):
    p, o, args = fixture(day)
    before = copy.deepcopy((p, o, args))
    r = review_eligibility(p, o, **args)
    assert r["eligible_for_source_reuse_review"] and r["candidate_day_kst"] == day
    assert all(
        r[k] is False
        for k in (
            "source_accepted",
            "conditional_reuse_approved",
            "original_database_write_authorized",
            "original_apply_ready",
            "orders_enabled",
            "live_trading_enabled",
        )
    )
    assert (p, o, args) == before


@pytest.mark.parametrize("day", ["2026-11-23", "2026-11-30"])
def test_dates_outside_reviewed_week_are_refused(day):
    p, o, args = fixture(day)
    with pytest.raises(SourceReuseBlocked, match="SOURCE_REUSE_PERIOD_INVALID"):
        review_eligibility(p, o, **args)


@pytest.mark.parametrize(
    "fault",
    [
        "wallet",
        "orders",
        "position",
        "income",
        "trade",
        "page",
        "sourcehash",
        "mode",
        "config",
        "state_claim",
        "wrongday",
        "false_zero",
        "missing",
    ],
)
def test_inconsistent_evidence_or_claims_are_refused(fault):
    p, o, args = fixture()
    s = o["summary"]
    if fault == "wallet":
        s["rounds"][1]["account"]["usdt_wallet"] = "5000.00000000"
    elif fault == "orders":
        s["rounds"][0]["regular_orders"]["count"] = 1
    elif fault == "position":
        s["rounds"][0]["positions"]["nonzero_positions"] = 1
    elif fault == "income":
        s["income_gap_rows"] = 1
    elif fault == "trade":
        s["btc_trade_gap_rows"] = 1
    elif fault == "page":
        s["income_gap_page_limit_reached"] = True
    elif fault == "sourcehash":
        o["saved_source_hashes_verified_before_after"] = False
    elif fault == "mode":
        s["mode_after"]["one_way"] = False
    elif fault == "config":
        s["btc_config_after"]["leverage"] = 3
    elif fault == "state_claim":
        o["equity_baseline_reconstructed"] = True
    elif fault == "wrongday":
        o["observed_day_key_kst"] = "2026-11-25"
    elif fault == "false_zero":
        s["btc_trade_gap_rows"] = False
    elif fault == "missing":
        del s["rounds"]
    with pytest.raises(SourceReuseBlocked):
        review_eligibility(p, o, **args)


@pytest.mark.parametrize(
    "fault", ["stale", "future", "gap", "midnight", "activated", "loosen_clock", "loosen_guard"]
)
def test_clock_reference_and_policy_cannot_be_relaxed(fault):
    p, o, args = fixture()
    policy = p["conditional_reuse"]
    if fault == "stale":
        args["server_now_ms"] = o["observations_end_ms"] + 300001
    elif fault == "future":
        args["server_now_ms"] = o["observations_end_ms"] - 1
    elif fault == "gap":
        args["verified_reference_start_ms"] = o["observations_end_ms"] - 604800001
    elif fault == "midnight":
        o["observations_end_ms"] = int(
            datetime(2026, 11, 24, 14, 59, 59, tzinfo=UTC).timestamp() * 1000
        )
        args.update(
            server_now_ms=o["observations_end_ms"] + 2000,
            verified_reference_start_ms=o["observations_end_ms"] - 60000,
        )
    elif fault == "activated":
        policy["activated"] = True
    elif fault == "loosen_clock":
        policy["max_observation_age_ms"] = 600000
    elif fault == "loosen_guard":
        policy["does_not_waive_existing_apply_guards"] = False
    with pytest.raises(SourceReuseBlocked):
        review_eligibility(p, o, **args)
