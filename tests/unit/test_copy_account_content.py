"""Narrow body arithmetic never grants account identity or baseline authority."""

import copy
import json

import pytest

from packages.persistence.demo_order_quota import LocalQuotaStopped
from services.execution.demo_copy_account_content import review_account_body


def account_body():
    zeros = dict(
        initialMargin="0", maintMargin="0", positionInitialMargin="0", openOrderInitialMargin="0"
    )
    return dict(
        totalWalletBalance="12.123456789012345678",
        totalMarginBalance="12.123456789012345678",
        totalUnrealizedProfit="0",
        availableBalance="12.123456789012345678",
        totalInitialMargin="0",
        totalMaintMargin="0",
        totalPositionInitialMargin="0",
        totalOpenOrderInitialMargin="0",
        assets=[
            dict(
                asset="USDT",
                walletBalance="12.123456789012345678",
                marginBalance="12.123456789012345678",
                availableBalance="12.123456789012345678",
                unrealizedProfit="0",
                **zeros,
            )
        ],
        positions=[
            dict(
                symbol="BTCUSDT",
                positionSide="BOTH",
                positionAmt="0",
                unrealizedProfit="0",
                **zeros,
            )
        ],
    )


def test_exact_decimal_arithmetic_never_returns_balance_identity_or_authority():
    body = account_body()
    body.update(uid="PRIVATE_UID", accountAlias="PRIVATE_ALIAS", multiAssetsMargin=False)
    result = review_account_body(json.dumps(body).encode())
    assert result["positive_usdt_wallet_observed"]
    assert all(
        not result[k]
        for k in (
            "account_configuration_verified",
            "open_orders_verified",
            "remote_account_identity_verified",
            "baseline_amount_proposed",
            "baseline_source_accepted",
            "baseline_adoption_approved",
            "execution_approved",
            "orders_enabled",
            "live_trading_enabled",
        )
    )
    assert "PRIVATE" not in str(result) and "12.123456789012345678" not in str(result)


@pytest.mark.parametrize(
    "value",
    [
        True,
        False,
        1,
        1.2,
        None,
        "NaN",
        "Infinity",
        "1e2",
        "+12",
        " 12",
        "01",
        "1,000",
        "-1",
        "0",
        "9" * 21,
        "1." + "0" * 19,
    ],
)
def test_unsafe_wallet_encodings_denied(value):
    body = account_body()
    body["totalWalletBalance"] = value
    with pytest.raises(LocalQuotaStopped):
        review_account_body(json.dumps(body).encode())


@pytest.mark.parametrize(
    "field",
    [
        "totalMarginBalance",
        "availableBalance",
        "totalUnrealizedProfit",
        "totalInitialMargin",
        "totalMaintMargin",
        "totalPositionInitialMargin",
        "totalOpenOrderInitialMargin",
    ],
)
def test_missing_required_totals_denied(field):
    body = account_body()
    del body[field]
    with pytest.raises(LocalQuotaStopped):
        review_account_body(json.dumps(body).encode())


@pytest.mark.parametrize(
    "fault",
    [
        "margin_total",
        "available",
        "pnl",
        "initial",
        "cross_wallet",
        "cross_pnl",
        "duplicate_asset",
        "missing_usdt",
        "other_asset_balance",
        "duplicate_position",
        "hedge",
        "position_amount",
        "position_margin",
        "position_notional",
        "asset_margin",
        "too_many_assets",
        "too_many_positions",
        "error_object",
    ],
)
def test_inconsistent_or_exposed_account_denied(fault):
    body = account_body()
    if fault == "margin_total":
        body["totalMarginBalance"] = "12"
    if fault == "available":
        body["availableBalance"] = "10"
    if fault == "pnl":
        body["totalUnrealizedProfit"] = "-1"
    if fault == "initial":
        body["totalInitialMargin"] = "1"
    if fault == "cross_wallet":
        body["totalCrossWalletBalance"] = "10"
    if fault == "cross_pnl":
        body["totalCrossUnPnl"] = "1"
    if fault == "duplicate_asset":
        body["assets"] *= 2
    if fault == "missing_usdt":
        body["assets"][0]["asset"] = "BNB"
    if fault == "other_asset_balance":
        other = copy.deepcopy(body["assets"][0])
        other["asset"] = "BNB"
        body["assets"].append(other)
    if fault == "duplicate_position":
        body["positions"] *= 2
    if fault == "hedge":
        body["positions"][0]["positionSide"] = "LONG"
    if fault == "position_amount":
        body["positions"][0]["positionAmt"] = "0.001"
    if fault == "position_margin":
        body["positions"][0]["maintMargin"] = "1"
    if fault == "position_notional":
        body["positions"][0]["notional"] = "-1"
    if fault == "asset_margin":
        body["assets"][0]["openOrderInitialMargin"] = "1"
    if fault == "too_many_assets":
        body["assets"] *= 33
    if fault == "too_many_positions":
        body["positions"] *= 501
    if fault == "error_object":
        body["code"] = -2015
    with pytest.raises(LocalQuotaStopped):
        review_account_body(json.dumps(body).encode())


@pytest.mark.parametrize(
    "body",
    [
        b"[]",
        b"null",
        b"{",
        b"\xff",
        b'{"x":NaN}',
        b'{"x":Infinity}',
        b'{"x":1,"x":2}',
        b'{"nested":{"x":1,"x":2}}',
        b"[" * 1100 + b"]" * 1100,
    ],
)
def test_malformed_duplicate_or_nonfinite_json_denied(body):
    with pytest.raises(LocalQuotaStopped):
        review_account_body(body)


def test_non_usdt_zero_assets_and_empty_flat_positions_are_content_only():
    body = account_body()
    body["positions"] = []
    zero = {k: ("BNB" if k == "asset" else "0") for k in body["assets"][0]}
    body["assets"].append(zero)
    result = review_account_body(json.dumps(body).encode())
    assert result["asset_rows_checked"] == 2 and result["position_rows_checked"] == 0
    assert not result["open_orders_verified"] and not result["account_configuration_verified"]


def test_body_limit_denied_before_json_parse():
    with pytest.raises(LocalQuotaStopped, match="BODY_LIMIT"):
        review_account_body(b" " * (4_194_304 + 1))


@pytest.mark.parametrize("field", ["dualSidePosition", "multiAssetsMargin"])
@pytest.mark.parametrize("value", [True, 1, 0, "false", None])
def test_present_account_mode_fields_must_be_exact_false(field, value):
    body = account_body()
    body[field] = value
    with pytest.raises(LocalQuotaStopped, match="UNSUPPORTED_ACCOUNT_MODE"):
        review_account_body(json.dumps(body).encode())
