"""Actual DEMO response shape: fill at .723, matching income at .000."""

from decimal import Decimal
from uuid import uuid4

import pytest

from services.execution.demo_exit_day_review import ExitDayBlocked
from services.execution.demo_models import ExitAnchoredDayBaseline
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_cancelled_correction import correction, event_count  # noqa: F401
from tests.integration.test_demo_day_reconstruction import day_case as parent_case  # noqa: F401
from tests.integration.test_demo_exit_day_review import apply, review
from tests.integration.test_demo_exit_day_review import exit_case as base_precision_case  # noqa: F401


@pytest.fixture
def precision_case(base_precision_case):  # noqa: F811
    _, _, _, rows, incomes, _ = base_precision_case
    rows[-1]["commission"] = "0.02288622"
    incomes[0]["income"] = "-0.02288622"
    return base_precision_case


def truncate(case):
    store, c, fake, rows, incomes, raw = case
    second = rows[-1]["time"] // 1000 * 1000
    for r in incomes:
        r["time"] = second
    return second


def test_second_truncated_income_matches_precise_signed_fill_without_writes(precision_case):
    store, c, fake, rows, *_ = precision_case
    assert rows[-1]["time"] % 1000 == 723
    truncate(precision_case)
    before = store.read("DEMO-test")
    count = event_count(store)
    start = len(fake.calls)
    report = review(precision_case)
    assert report["known_exit_utc"].endswith("56.723000+00:00")
    assert report["exit_income_second_start_utc"].endswith("56+00:00")
    assert report["income_time_basis"] == "SECOND_BUCKET_OR_EXACT_FILL_TIMESTAMP"
    assert report["account_wide_exchange_export_verified"] is False
    assert report["proposed_daily_base_usdt"] == str(before.value.equity)
    assert store.read("DEMO-test") == before and event_count(store) == count
    assert all(m == "GET" for m, _, _ in fake.calls[start:])


def test_precision_basis_receipt_is_audited_without_rewriting_observation(precision_case):
    store, c, fake, *_ = precision_case
    truncate(precision_case)
    before = store.read("DEMO-test")
    operation = uuid4()
    assert apply(precision_case, review(precision_case)["review_token"], operation=operation) == (
        "EXIT_ANCHORED_DAILY_BASELINE_ADOPTED_KEEP_LOCK"
    )
    after = store.read("DEMO-test")
    receipt = after.exit_anchored_day_baselines[str(operation)]
    assert receipt.known_exit_utc.microsecond == 723000
    assert receipt.observation_utc == before.last_reconciled
    assert after.value == before.value and after.last_reconciled == before.last_reconciled
    assert after.requests == before.requests and after.orders == before.orders
    assert after.paused and after.risk_state.value == "EMERGENCY"
    assert receipt.income_time_basis == "SECOND_BUCKET_OR_EXACT_FILL_TIMESTAMP"
    legacy = receipt.model_dump()
    legacy.pop("income_time_basis")
    assert (
        ExitAnchoredDayBaseline.model_validate(legacy).income_time_basis == "EXACT_FILL_TIMESTAMP"
    )


@pytest.mark.parametrize("offset", [-1000, 1, 500, 1000])
def test_no_general_time_tolerance_for_income(precision_case, offset):
    second = truncate(precision_case)
    store, c, fake, rows, incomes, *_ = precision_case
    incomes[0]["time"] = second + offset
    with pytest.raises(ExitDayBlocked):
        review(precision_case)


@pytest.mark.parametrize("offset", [0, 100, 722, 723, 724, 999, 1000])
def test_other_btc_trade_in_bucket_or_later_remains_blocked(precision_case, offset):
    second = truncate(precision_case)
    store, c, fake, rows, *_ = precision_case
    other = dict(rows[-1])
    other.update(id=900, orderId=9999, time=second + offset)
    rows.append(other)
    with pytest.raises(ExitDayBlocked):
        review(precision_case)


@pytest.mark.parametrize(
    "change",
    ["fee", "pnl", "extra_zero", "offsetting", "foreign_symbol", "transfer", "missing_fee"],
)
def test_second_precision_does_not_relax_amount_or_activity_guards(precision_case, change):
    truncate(precision_case)
    store, c, fake, rows, incomes, *_ = precision_case
    if change == "fee":
        incomes[0]["income"] = "-0.01"
    elif change == "pnl":
        incomes[1]["income"] = "0"
    elif change == "extra_zero":
        extra = dict(incomes[0], tranId="900", income="0")
        incomes.append(extra)
    elif change == "offsetting":
        incomes.extend(
            [
                dict(incomes[1], tranId="901", income="1"),
                dict(incomes[1], tranId="902", income="-1"),
            ]
        )
    elif change == "foreign_symbol":
        incomes[0]["symbol"] = "ETHUSDT"
    elif change == "transfer":
        incomes.append(dict(incomes[0], tranId="903", incomeType="TRANSFER", income="0"))
    elif change == "missing_fee":
        incomes.pop(0)
    with pytest.raises(ExitDayBlocked):
        review(precision_case)


def test_precise_and_second_timestamps_can_coexist_for_same_known_exit(precision_case):
    store, c, fake, rows, incomes, *_ = precision_case
    incomes[0]["time"] = rows[-1]["time"] // 1000 * 1000
    assert review(precision_case)["post_exit_income_records"] == 0


def test_multiple_fills_in_second_require_all_individual_fee_and_pnl_records(precision_case):
    store, c, fake, rows, incomes, *_ = precision_case
    second = truncate(precision_case)
    original = rows[-1]
    quantity = Decimal(original["qty"])
    original.update(qty=str(quantity / 2), commission="0.01", realizedPnl="-0.02")
    other = dict(original, id=900, time=second + 700, commission="0.02", realizedPnl="-0.03")
    rows.append(other)
    incomes[:] = [
        dict(
            asset="USDT",
            symbol="BTCUSDT",
            time=second,
            tranId=str(i),
            incomeType=kind,
            income=amount,
        )
        for i, (kind, amount) in enumerate(
            [
                ("COMMISSION", "-0.01"),
                ("COMMISSION", "-0.02"),
                ("REALIZED_PNL", "-0.02"),
                ("REALIZED_PNL", "-0.03"),
            ],
            start=100,
        )
    ]
    assert review(precision_case)["income_time_basis"] == "SECOND_BUCKET_OR_EXACT_FILL_TIMESTAMP"
    incomes[0]["income"] = "-0.03"
    incomes.pop(1)
    with pytest.raises(ExitDayBlocked, match="EXIT_INCOME_DOES_NOT_MATCH_FILLS"):
        review(precision_case)
