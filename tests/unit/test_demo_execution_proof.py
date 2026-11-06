"""Exact two-execution Windows evidence; malformed sets never become valid fills."""

from decimal import Decimal as D

import pytest

from integrations.binance_usdm.demo import DemoFailure
from services.execution.demo_execution_proof import execution_proof

NOW = 1793416672499


def row(identifier=1, **updates):
    return (
        dict(
            symbol="BTCUSDT",
            orderId="123",
            id=identifier,
            time=NOW - 2640,
            positionSide="BOTH",
            side="BUY",
            commissionAsset="USDT",
            qty="0.0003",
            price="77296.90",
            commission="0.00463781",
            realizedPnl="0",
        )
        | updates
    )


def prove(rows, **updates):
    return execution_proof(
        rows,
        **(
            dict(
                order_id="123",
                side="BUY",
                quantity=D("0.0006"),
                price=D("77296.90"),
                fee_rate=D("0.0004"),
                now_ms=NOW,
                max_age_ms=30000,
            )
            | updates
        ),
    )


def test_actual_two_equal_price_executions_sum_exactly_and_sort_for_evidence():
    a, b = row(), row(2, time=NOW)
    proof, fee = prove([b, a])
    assert fee == D("0.00927562")
    assert sum(D(r["quantity"]) for r in proof) == D("0.0006")
    assert [r["id"] for r in proof] == ["1", "2"]
    assert prove([a, b]) == (proof, fee)


@pytest.mark.parametrize(
    "rows", [[], None, {}, [None], [row()] * 33, [row()], [row(), row()], [row(), row(2), row(3)]]
)
def test_empty_duplicate_oversized_or_incomplete_sets_fail(rows):
    with pytest.raises(DemoFailure):
        prove(rows)


@pytest.mark.parametrize(
    "updates",
    [
        {"symbol": "ETHUSDT"},
        {"orderId": "999"},
        {"orderId": True},
        {"side": "SELL"},
        {"positionSide": "LONG"},
        {"commissionAsset": "BNB"},
        {"id": True},
        {"id": "bad"},
        {"time": True},
        {"time": NOW + 1},
        {"time": NOW - 30001},
        {"qty": "0"},
        {"qty": "-1"},
        {"price": "77296.80"},
        {"price": "NaN"},
        {"commission": "-1"},
        {"commission": "1"},
        {"realizedPnl": "0.01"},
    ],
)
def test_invalid_execution_cannot_hide_in_other_valid_rows(updates):
    with pytest.raises(DemoFailure):
        prove([row(), row(2, **updates)])


def test_distinct_prices_are_rejected_without_guessing_exchange_average_rounding():
    with pytest.raises(DemoFailure):
        prove([row(price="77296.80"), row(2, price="77297.00")])


def test_short_same_price_and_exact_fee_boundary_are_supported():
    rows = [row(side="SELL", commission="0.009275628"), row(2, side="SELL", commission="0")]
    _, fee = prove(rows, side="SELL")
    assert fee == D("0.009275628")
