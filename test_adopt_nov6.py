import copy
from datetime import UTC, datetime

import pytest

import adopt_nov6_day as m

NOW = datetime(2026, 11, 6, 0, tzinfo=UTC)
SAFE = dict(risk_state="NORMAL", paused=True, orders_enabled=False, live_trading_enabled=False)
PERIODS = dict(
    stored_day_kst="2026-11-05",
    current_day_kst=m.DATE,
    stored_week_kst="2026-11-02",
    current_week_kst="2026-11-02",
)
LOCAL = dict(
    SAFE, recovery="LOCAL_PERIOD_CHECK_NO_DATABASE_CHANGE", period_keys_match=False, periods=PERIODS
)
PREVIEW = dict(
    SAFE,
    review="PAUSED_NORMAL_DAY_PREVIEW_NO_DATABASE_CHANGE",
    account_id=m.ACCOUNT,
    source="OPERATOR_ATTESTED_FLAT_WITH_SIGNED_USDT_EVIDENCE",
    old_daily_key="2026-11-05",
    proposed_day_kst=m.DATE,
    database_changed=False,
    account_wide_exchange_export_verified=False,
    operator_attestation_used=True,
    weekly_base_preserved=True,
    high_water_preserved=True,
    old_valuation_timestamp_preserved=True,
    requests_preserved=True,
    risk_state_preserved=True,
    old_daily_base_usdt="4998.99761493",
    proposed_daily_base_usdt="4998.95579884",
    net_deposit_basis_usdt="0",
    boundary_utc="2026-11-05T15:00:00+00:00",
    anchor_utc="2026-11-05T10:22:12.883392+00:00",
    known_exit_utc="2026-11-05T10:21:53.432000+00:00",
    attested_until_utc="2026-11-06T00:06:21+00:00",
    review_token="ab" * 32,
)
APPLIED = dict(
    SAFE,
    day_baseline=m.ADOPTED,
    operation_id=m.OPERATION,
    leader_released=True,
    unresolved_requests_acknowledged=False,
    weekly_base_preserved=True,
    high_water_preserved=True,
)
READY = dict(
    SAFE,
    session_ready="READ_ONLY_READY_ENTRIES_STILL_PAUSED",
    exchange_empty_verified=True,
    database_changed=False,
    period_keys_match=True,
    periods={**PERIODS, "stored_day_kst": m.DATE},
)


def invoke_with(reports):
    calls = []

    def invoke(*args):
        calls.append(args)
        value = reports[len(calls) - 1]
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value)

    return invoke, calls


def test_token_passed_exactly_and_fixed_operation_and_postcheck():
    invoke, calls = invoke_with([LOCAL, PREVIEW, APPLIED, READY])
    result = m.run(invoke, NOW)
    assert result["daily_update"] == "NOV6_DAY_ADOPTED_AND_READY_ENTRIES_STILL_PAUSED"
    assert calls[2] == (
        "review_demo_paused_day",
        "--attest-no-other-trades-since-exit",
        "--accept-operator-evidence",
        "--apply-review",
        PREVIEW["review_token"],
        "--operation-id",
        m.OPERATION,
    )
    assert calls[3] == ("check_demo_session_ready",)


@pytest.mark.parametrize(
    "change",
    [
        {"paused": False},
        {"risk_state": "EMERGENCY"},
        {"account_id": "OTHER"},
        {"proposed_day_kst": "2026-11-07"},
        {"proposed_daily_base_usdt": "4999"},
        {"weekly_base_preserved": False},
        {"requests_preserved": False},
        {"operator_attestation_used": False},
        {"review_token": "bad"},
        {"net_deposit_basis_usdt": "1"},
        {"old_daily_base_usdt": "4999"},
        {"known_exit_utc": "2026-11-01T01:30:26+00:00"},
        {"anchor_utc": "2026-11-03T02:17:15+00:00"},
        {"boundary_utc": "2026-11-04T15:00:00+00:00"},
    ],
)
def test_changed_candidate_never_calls_apply(change):
    invoke, calls = invoke_with([LOCAL, {**PREVIEW, **change}])
    result = m.run(invoke, NOW)
    assert result["daily_update"] == "BLOCKED_NO_AUTOMATIC_RETRY"
    assert result["apply_started"] is False and len(calls) == 2


def test_stale_date_never_queries():
    invoke, calls = invoke_with([])
    assert m.run(invoke, datetime(2026, 11, 7, tzinfo=UTC))["apply_started"] is False
    assert not calls


def test_already_current_only_reads_no_apply():
    invoke, calls = invoke_with(
        [{**LOCAL, "period_keys_match": True, "periods": READY["periods"]}, READY]
    )
    assert m.run(invoke, NOW)["daily_update"] == "ALREADY_CURRENT_NO_APPLY"
    assert len(calls) == 2 and calls[-1] == ("check_demo_session_ready",)


@pytest.mark.parametrize(
    "failure", [ValueError("PRIVATE_SECRET_OUTPUT"), {**APPLIED, "leader_released": False}]
)
def test_uncertain_apply_never_retries_or_leaks(failure):
    invoke, calls = invoke_with([LOCAL, PREVIEW, failure])
    result = m.run(invoke, NOW)
    assert result["application_may_have_completed"] is True and len(calls) == 3
    assert "PRIVATE_SECRET_OUTPUT" not in str(result)


def test_postcheck_failure_preserves_applied_possibility():
    invoke, calls = invoke_with(
        [LOCAL, PREVIEW, APPLIED, {**READY, "exchange_empty_verified": False}]
    )
    result = m.run(invoke, NOW)
    assert result["stage"] == "POST_APPLY_READ_ONLY_READY"
    assert result["application_may_have_completed"] is True and len(calls) == 4
