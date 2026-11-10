"""Pinned documentation, cost assumptions and query scope must agree exactly."""

import json
from pathlib import Path

import pytest

from integrations.binance_usdm.demo_tp_weight_model import MODELED_TP_ROUTE_COSTS
from packages.persistence.demo_order_quota import LocalQuotaStopped
from services.execution import demo_query_contract as query
from services.execution.demo_runtime_cost_contract import (
    RULES,
    cost_contract_report,
    require_runtime_cost_contract,
)
from tests.fixtures.tp_admission_synthetic_exchange import COSTS


def test_complete_policy_retains_independent_synthetic_meter_and_no_execution_approval():
    require_runtime_cost_contract(MODELED_TP_ROUTE_COSTS)
    assert MODELED_TP_ROUTE_COSTS == COSTS
    report = cost_contract_report(MODELED_TP_ROUTE_COSTS)
    assert report["documented_routes_covered"] == 21
    assert report["conservative_mutation_weight_retained"] == 40
    assert not report["venue_request_cost_upper_bounds_proven"]
    assert not report["actual_order_usage_verified"]
    assert not report["external_ip_capacity_reserved"] and not report["execution_approved"]


@pytest.mark.parametrize("rule", RULES, ids=lambda rule: rule.method + " " + rule.path)
@pytest.mark.parametrize("change", ["missing", "lower", "higher", "bool", "float"])
def test_every_initial_route_requires_the_reviewed_integer_cost(rule, change):
    costs = dict(MODELED_TP_ROUTE_COSTS)
    key = rule.method, rule.path
    if change == "missing":
        del costs[key]
    elif change == "lower":
        costs[key] = rule.modeled_weight - 1
    elif change == "higher":
        costs[key] = rule.modeled_weight + 1
    elif change == "bool":
        costs[key] = True
    else:
        costs[key] = float(rule.modeled_weight)
    with pytest.raises(LocalQuotaStopped, match="COST_CONTRACT_MODEL_MISMATCH"):
        require_runtime_cost_contract(costs)


@pytest.mark.parametrize("scope", ["public", "global", "signing", "version"])
def test_query_policy_drift_cannot_keep_an_old_cost_claim(monkeypatch, scope):
    if scope == "public":
        monkeypatch.setitem(query.PUBLIC, "/fapi/v1/premiumIndex", {})
    elif scope == "global":
        monkeypatch.setattr(query, "GLOBAL_GET", frozenset())
    elif scope == "signing":
        monkeypatch.setattr(query, "SIGNING", frozenset())
    else:
        monkeypatch.setattr(query, "CONTRACT_VERSION", "UNREVIEWED")
    with pytest.raises(LocalQuotaStopped, match="COST_CONTRACT_QUERY_POLICY_MISMATCH"):
        require_runtime_cost_contract(MODELED_TP_ROUTE_COSTS)


def test_all_rules_have_the_verified_official_documentation_reference():
    path = (
        Path(__file__).resolve().parents[2]
        / "docs/request_cost_evidence/PINNED_RUNTIME_COST_PROOF.json"
    )
    evidence = json.loads(path.read_text())
    assert evidence["sdk_commit"] == "f75b4a52c5aebf52b1c6ad671b0ce53b96f8e179"
    assert set(evidence["files"]) == {
        "account_api.py",
        "trade_api.py",
        "market_data_api.py",
        "user_data_streams_api.py",
    }
    rows = {(row["method"], row["path"]): row for row in evidence["routes"]}
    assert set(rows) == set(MODELED_TP_ROUTE_COSTS) and len(rows) == 21
    for rule in RULES:
        row = rows[rule.method, rule.path]
        assert row["sdk_file"] == rule.sdk_file and row["sdk_method"] == rule.sdk_method
        assert row["modeled_weight"] == rule.modeled_weight
        assert row["documented_ip_weight_for_approved_shape"] == rule.documented_ip_weight
        assert rule.modeled_weight >= rule.documented_ip_weight
        assert "Weight" in row["weight_excerpt"]
        assert evidence["files"][rule.sdk_file]["url"].startswith(
            "https://raw.githubusercontent.com/binance/binance-connector-python/"
            + evidence["sdk_commit"]
            + "/"
        )
    for method in ["POST", "PUT", "DELETE"]:
        assert MODELED_TP_ROUTE_COSTS[method, "/fapi/v1/listenKey"] == 40
    for path in ["/fapi/v1/order", "/fapi/v1/algoOrder"]:
        row = rows["POST", path]
        assert row["documented_ip_weight_for_approved_shape"] == 0
        assert row["order_count_weight_10s"] == row["order_count_weight_1m"] == 1
        assert row["modeled_weight"] == 40


def test_documented_weights_are_recoverable_from_pinned_sdk_excerpts():
    import re

    path = (
        Path(__file__).resolve().parents[2]
        / "docs/request_cost_evidence/PINNED_RUNTIME_COST_PROOF.json"
    )
    data = json.loads(path.read_text())
    for row in data["routes"]:
        text = row["weight_excerpt"]
        method = row["sdk_method"]
        if method in {"new_order", "new_algo_order"}:
            match = re.search(r"(\d+) on IP rate limit", text)
        elif method in {"current_all_open_orders", "current_all_algo_open_orders"}:
            match = re.search(r"\*\*(\d+)\*\* when the symbol parameter is omitted", text)
        elif method == "mark_price":
            match = re.search(r"\*\*(\d+)\*\* with symbol", text)
        elif method == "symbol_order_book_ticker":
            match = re.search(r"\*\*(\d+)\*\* for a single symbol", text)
        elif method == "kline_candlestick_data":
            match = re.search(r"\[1,100\).*?\|\s*(\d+)", text)
        else:
            match = re.search(r"Weight\(IP\): (\d+)", text)
        assert match is not None, row["path"]
        assert int(match.group(1)) == row["documented_ip_weight_for_approved_shape"]
