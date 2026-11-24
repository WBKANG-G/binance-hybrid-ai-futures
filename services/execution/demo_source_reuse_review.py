"""Pure eligibility review for a PENDING bounded source policy; never grants authority."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal


class SourceReuseBlocked(ValueError):
    pass


def require(ok, code):
    if not ok:
        raise SourceReuseBlocked(code)


def zero_count(value):
    return type(value) is int and value == 0


def review_eligibility(proposal, observation, *, verified_reference_start_ms, server_now_ms):
    """Caller must supply hash-verified evidence and a fresh trusted server clock.

    A positive result only permits presenting a candidate for source review. It
    never changes source consent, original apply readiness, or any ledger.
    """
    try:
        policy = proposal["conditional_reuse"]
        require(
            policy["scope"] == "SOURCE_REVIEW_ONLY_WITHIN_SAME_WEEK"
            and policy["activated"] is False
            and policy["first_day_kst"] == "2026-11-24"
            and policy["last_day_kst"] == "2026-11-29"
            and policy["week_kst"] == "2026-11-23"
            and policy["fixed_usdt"] == "4998.74183608"
            and type(policy["max_observation_age_ms"]) is int
            and policy["max_observation_age_ms"] == 300000
            and type(policy["max_verified_reference_gap_ms"]) is int
            and policy["max_verified_reference_gap_ms"] == 604800000,
            "SOURCE_REUSE_POLICY_INVALID",
        )
        for key in (
            "requires_new_verified_observation_for_each_day",
            "requires_two_flat_stable_account_rounds",
            "requires_no_income_or_btc_trade_gap_rows",
            "requires_unsaturated_gap_pages",
            "requires_verified_source_hashes",
            "does_not_waive_existing_apply_guards",
        ):
            require(policy[key] is True, "SOURCE_REUSE_POLICY_INVALID")
        end = observation["observations_end_ms"]
        require(
            type(end) is int
            and type(server_now_ms) is int
            and type(verified_reference_start_ms) is int
            and 0 <= server_now_ms - end <= policy["max_observation_age_ms"]
            and 0 <= end - verified_reference_start_ms <= policy["max_verified_reference_gap_ms"],
            "SOURCE_REUSE_CLOCK_OR_GAP_INVALID",
        )
        at = datetime.fromtimestamp(end / 1000, UTC) + timedelta(hours=9)
        now = datetime.fromtimestamp(server_now_ms / 1000, UTC) + timedelta(hours=9)
        day = str(at.date())
        week = str((at - timedelta(days=at.weekday())).date())
        require(
            policy["first_day_kst"] <= day <= policy["last_day_kst"]
            and str(now.date()) == day
            and week == policy["week_kst"]
            and observation["observed_day_key_kst"] == day
            and observation["observed_week_key_kst"] == week,
            "SOURCE_REUSE_PERIOD_INVALID",
        )
        require(
            observation["current_read"] == "CURRENT_ACCOUNT_OBSERVATIONS_SAVED_NO_CHANGES"
            and observation["saved_source_hashes_verified_before_after"] is True
            and observation["open_order_and_current_position_scope"] == "ALL_SYMBOLS"
            and observation["btc_configuration_and_trade_history_scope"] == "BTCUSDT",
            "SOURCE_REUSE_EVIDENCE_INVALID",
        )
        for flag in (
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
            require(observation[flag] is False, "SOURCE_REUSE_CLAIM_INVALID")
        require(zero_count(observation["order_writes"]), "SOURCE_REUSE_CLAIM_INVALID")
        summary = observation["summary"]
        require(
            summary["issues"] == []
            and summary["observations_consistent_with_expected_flat_profile"] is True,
            "SOURCE_REUSE_PROFILE_INVALID",
        )
        for flag in ("mode_equal_at_reads", "btc_config_equal_at_reads", "account_equal_at_reads"):
            require(summary[flag] is True, "SOURCE_REUSE_PROFILE_INVALID")
        for name in ("before", "after"):
            mode = summary["mode_" + name]
            cfg = summary["btc_config_" + name]
            require(
                all(mode[x] is True for x in ("one_way", "single_asset", "account_can_trade")),
                "SOURCE_REUSE_PROFILE_INVALID",
            )
            require(
                cfg["symbol"] == "BTCUSDT"
                and type(cfg["leverage"]) is int
                and cfg["leverage"] == 2
                and cfg["margin_type"] == "ISOLATED"
                and cfg["automatic_margin_addition"] is False,
                "SOURCE_REUSE_PROFILE_INVALID",
            )
        require(
            summary["mode_before"] == summary["mode_after"]
            and summary["btc_config_before"] == summary["btc_config_after"],
            "SOURCE_REUSE_PROFILE_INVALID",
        )
        require(len(summary["rounds"]) == 2, "SOURCE_REUSE_PROFILE_INVALID")
        for row in summary["rounds"]:
            for kind in ("regular_orders", "algo_orders"):
                require(
                    zero_count(row[kind]["count"]) and row[kind]["empty"] is True,
                    "SOURCE_REUSE_PROFILE_INVALID",
                )
            pos, account = row["positions"], row["account"]
            require(
                pos["flat"] is True
                and all(
                    zero_count(pos[x])
                    for x in ("nonzero_positions", "nonzero_non_btc_positions", "hedge_side_rows")
                ),
                "SOURCE_REUSE_PROFILE_INVALID",
            )
            require(
                all(
                    type(account[x]) is str and account[x] == policy["fixed_usdt"]
                    for x in ("usdt_wallet", "usdt_available", "usdt_margin_balance")
                ),
                "SOURCE_REUSE_AMOUNT_INVALID",
            )
            require(
                account["positions_flat"] is True
                and account["flat_unencumbered_usdt"] is True
                and all(
                    type(account[x]) is str and Decimal(account[x]) == 0
                    for x in ("total_initial_margin", "total_unrealized_profit")
                ),
                "SOURCE_REUSE_PROFILE_INVALID",
            )
        require(
            summary["rounds"][0]["account"] == summary["rounds"][1]["account"],
            "SOURCE_REUSE_PROFILE_INVALID",
        )
        require(
            all(zero_count(summary[x]) for x in ("income_gap_rows", "btc_trade_gap_rows"))
            and all(
                summary[x] is False
                for x in ("income_gap_page_limit_reached", "trade_gap_page_limit_reached")
            ),
            "SOURCE_REUSE_ACTIVITY_INVALID",
        )
        return dict(
            eligible_for_source_reuse_review=True,
            candidate_day_kst=day,
            candidate_week_kst=week,
            candidate_usdt=policy["fixed_usdt"],
            source_accepted=False,
            conditional_reuse_approved=False,
            original_database_write_authorized=False,
            original_apply_ready=False,
            orders_enabled=False,
            live_trading_enabled=False,
        )
    except SourceReuseBlocked:
        raise
    except (KeyError, TypeError, ValueError, OverflowError, ArithmeticError):
        raise SourceReuseBlocked("SOURCE_REUSE_INPUT_INVALID") from None
