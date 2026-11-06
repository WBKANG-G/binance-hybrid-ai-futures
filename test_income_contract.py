"""Synthetic coverage contracts; never live venue evidence."""

import importlib.util
from decimal import Decimal
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "income_contract", Path(__file__).with_name("income_contract.py")
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def row(i, stamp, kind="COMMISSION", amount="-0.01", asset="USDT"):
    return {"tranId": i, "time": stamp, "incomeType": kind, "income": amount, "asset": asset}


class Venue:
    def __init__(self, data):
        self.data, self.calls = data, []
        self.ignore_filter = False
        self.drop_bonus = False
        self.ignore_time = False
        self.fail_at = None

    def __call__(self, method, path, params):
        assert method == "GET" and path == "/fapi/v1/income"
        assert "page" not in params and "symbol" not in params
        self.calls.append(dict(params))
        if len(self.calls) == self.fail_at:
            raise RuntimeError("synthetic upstream failure")
        selected = [
            r
            for r in self.data
            if self.ignore_time or int(params["startTime"]) <= r["time"] <= int(params["endTime"])
        ]
        if "incomeType" in params and not self.ignore_filter:
            selected = [r for r in selected if r["incomeType"] == params["incomeType"]]
        if self.drop_bonus and params.get("incomeType") == "WELCOME_BONUS":
            selected = []
        return selected[:1000]


@pytest.mark.parametrize("n", [0, 1, 12, 999])
def test_small_history_keeps_single_unfiltered_read(n):
    data = [row(i + 1, i) for i in range(n)]
    v = Venue(data)
    result = m.scan(v, 0, 10000)
    assert result["transfers"] == {} and result["http_attempts"] == 1
    assert "incomeType" not in v.calls[0]


@pytest.mark.parametrize("strategy", ["adaptive_filtered", "unfiltered_split"])
def test_positive_transfer_bonus_inclusive_boundaries_and_type_scoped_ids(strategy):
    data = [
        row(1, 0, "TRANSFER", "100"),
        row(1, 100, "WELCOME_BONUS", "2.5"),
        row(2, 50, "TRANSFER", "-20"),
    ]
    v = Venue(data)
    r = m.scan(v, 0, 100, strategy=strategy)
    assert r["transfers"] == {"TRANSFER:1": "100", "WELCOME_BONUS:1": "2.5", "TRANSFER:2": "-20"}
    assert sum(map(Decimal, r["transfers"].values())) == Decimal("82.5")


def test_sparse_targets_in_long_fee_history_use_three_queries():
    data = [row(i + 1, i) for i in range(9001)] + [
        row(10001, 3, "TRANSFER", "100"),
        row(10002, 8999, "WELCOME_BONUS", "2"),
    ]
    v = Venue(data)
    r = m.scan(v, 0, 10000)
    assert r["http_attempts"] == 3 and r["transfers"] == {
        "TRANSFER:10001": "100",
        "WELCOME_BONUS:10002": "2",
    }
    assert not r["server_filter_support_proven"] and not r["runtime_switch_approved"]


@pytest.mark.parametrize("strategy", ["adaptive_filtered", "unfiltered_split"])
def test_dense_targets_are_partitioned_without_timestamp_gaps(strategy):
    data = [row(i + 1, i, "TRANSFER", "1") for i in range(2501)]
    v = Venue(data)
    r = m.scan(v, 0, 2500, strategy=strategy)
    assert len(r["transfers"]) == 2501
    assert Decimal(r["transfer_sum_usdt"]) == 2501
    assert r["http_attempts"] > 3


@pytest.mark.parametrize("strategy", ["adaptive_filtered", "unfiltered_split"])
def test_full_single_millisecond_is_incomplete_and_returns_no_partial_success(strategy):
    v = Venue([row(i + 1, 50, "TRANSFER", "1") for i in range(1000)])
    with pytest.raises(m.ContractBlocked, match="DENSE_MILLISECOND_UNRESOLVED"):
        m.scan(v, 50, 50, strategy=strategy)


def test_filter_ignored_is_rejected():
    v = Venue([row(i + 1, i) for i in range(1000)])
    v.ignore_filter = True
    with pytest.raises(m.ContractBlocked, match="FILTER_RESPONSE_MISMATCH"):
        m.scan(v, 0, 1000)


def test_target_visible_in_unfiltered_prefix_cannot_disappear():
    v = Venue([row(10001, 0, "WELCOME_BONUS", "2")] + [row(i + 1, i) for i in range(1200)])
    v.drop_bonus = True
    with pytest.raises(m.ContractBlocked, match="UNFILTERED_PREFIX_TARGET_MISSING"):
        m.scan(v, 0, 2000)


@pytest.mark.parametrize("amount", ["NaN", "Infinity", "-Infinity", "abc", True, None])
def test_nonfinite_or_invalid_transfer_amount_is_rejected(amount):
    v = Venue([row(1, 1, "TRANSFER", amount)])
    with pytest.raises(m.ContractBlocked):
        m.scan(v, 0, 2)


@pytest.mark.parametrize("asset", ["BTC", "USDC", None])
def test_non_usdt_target_is_not_silently_dropped(asset):
    with pytest.raises(m.ContractBlocked, match="NON_USDT_TARGET"):
        m.scan(Venue([row(1, 1, "TRANSFER", "1", asset)]), 0, 2)


def test_conflicting_duplicate_id_is_not_last_write_wins():
    with pytest.raises(m.ContractBlocked, match="CONFLICTING_TRANSFER_ID"):
        m.scan(Venue([row(1, 1, "TRANSFER", "1"), row(1, 1, "TRANSFER", "2")]), 0, 2)


def test_identical_duplicate_is_counted_once():
    r = m.scan(Venue([row(1, 1, "TRANSFER", "1")] * 2), 0, 2)
    assert r["transfer_sum_usdt"] == "1" and len(r["transfers"]) == 1


def test_unknown_types_remain_outside_existing_target_accounting():
    r = m.scan(Venue([row(1, 1, "NEW_UNKNOWN_TYPE", "-10")]), 0, 2)
    assert r["transfers"] == {} and not r["all_financial_types_classified"]


@pytest.mark.parametrize("budget", [1, 2])
def test_request_budget_has_no_extra_retry_or_partial_success(budget):
    v = Venue([row(i + 1, i) for i in range(2000)])
    with pytest.raises(m.ContractBlocked, match="REQUEST_BUDGET_EXHAUSTED"):
        m.scan(v, 0, 3000, max_requests=budget)
    assert len(v.calls) == budget


def test_upstream_error_is_not_retried():
    v = Venue([row(i + 1, i) for i in range(2000)])
    v.fail_at = 2
    with pytest.raises(m.ContractBlocked, match="UPSTREAM_READ_FAILED_NO_RETRY"):
        m.scan(v, 0, 3000)
    assert len(v.calls) == 2


@pytest.mark.parametrize("start,end", [(2, 1), (-1, 2), (True, 3), (0, 604800001)])
def test_invalid_window_before_any_request(start, end):
    v = Venue([])
    with pytest.raises(m.ContractBlocked):
        m.scan(v, start, end)
    assert not v.calls


def test_out_of_window_response_is_rejected():
    v = Venue([row(1, 99, "TRANSFER", "1")])
    v.ignore_time = True
    with pytest.raises(m.ContractBlocked, match="ROW_OUTSIDE_FIXED_WINDOW"):
        m.scan(v, 0, 2)


@pytest.mark.parametrize("size", [12, 999, 1000, 2501, 9001])
@pytest.mark.parametrize("strategy", ["adaptive_filtered", "unfiltered_split"])
def test_transfer_map_matches_deployed_snapshot_parser_and_preserves_financial_snapshot(
    size, strategy
):
    import json
    from datetime import UTC, datetime
    from urllib.parse import parse_qs

    from pydantic import SecretStr

    from integrations.binance_usdm.demo import DemoClient, DemoCredentials
    from integrations.binance_usdm.demo_snapshot import snapshot
    from services.execution.demo_models import DemoLedger
    from services.risk.paper import AccountValue
    from tests.fixtures.demo import FakeDemo
    from tests.integration.test_demo import settings

    start = 1793960000000
    now = datetime.fromtimestamp((start + 20000) / 1000, UTC)
    data = [row(i + 1, start + i) for i in range(size)]
    data.extend(
        [
            row(100001, start, "TRANSFER", "100"),
            row(100001, start + 20000, "WELCOME_BONUS", "2.5"),
            row(100002, start + 100, "TRANSFER", "-20"),
        ]
    )
    fake = FakeDemo(now)

    def legacy_sender(method, path, query, key):
        if path == "/fapi/v1/income":
            params = {k: v[0] for k, v in parse_qs(query.get_secret_value()).items()}
            selected = [
                r for r in data if int(params["startTime"]) <= r["time"] <= int(params["endTime"])
            ]
            page, limit = int(params["page"]), int(params["limit"])
            return fake.response(selected[(page - 1) * limit : page * limit])
        return fake(method, path, query, key)

    client = DemoClient(
        settings().model_copy(update={"demo_trading_enabled": False}),
        DemoCredentials(SecretStr("synthetic"), SecretStr("synthetic")),
        send=legacy_sender,
        clock_ms=lambda: int(now.timestamp() * 1000),
    )
    client.sync_clock()
    ledger = DemoLedger(
        policy=settings().policy,
        value=AccountValue(as_of=now, initial="10000"),
        daily_base="10000",
        weekly_base="10000",
        high_water="10000",
        daily_key="2026-11-07",
        weekly_key="2026-11-02",
        income_since_ms=start,
    )
    old = snapshot(client, ledger, now)
    candidate = m.scan(Venue(data), start, start + 20000, strategy=strategy)
    assert candidate["transfers"] == old.transfers
    before = ledger.model_dump_json()
    assert (
        sum(map(Decimal, old.transfers.values()))
        == Decimal(candidate["transfer_sum_usdt"])
        == Decimal("82.5")
    )
    assert ledger.model_dump_json() == before
    assert old.equity == old.wallet_balance == Decimal("10000")
    assert old.positions == {} and old.orders == {} and old.algos == {}
    assert all(method == "GET" for method, _, _ in fake.calls)
    assert json.loads(old.model_dump_json())["transfers"] == candidate["transfers"]


def test_silent_filter_omission_outside_prefix_cannot_be_called_server_proof():
    data = [row(i + 1, i) for i in range(1500)] + [row(10000, 2000, "WELCOME_BONUS", "2")]
    v = Venue(data)
    v.drop_bonus = True
    r = m.scan(v, 0, 3000)
    # A malicious/incompatible reader can hide a row absent from the first prefix.
    # This deliberately demonstrates why synthetic coverage cannot approve runtime use.
    assert r["transfers"] == {} and not r["server_filter_support_proven"]
    assert not r["runtime_switch_approved"]
    baseline = m.scan(Venue(data), 0, 3000, strategy="unfiltered_split")
    assert baseline["transfers"] == {"WELCOME_BONUS:10000": "2"}


@pytest.mark.parametrize("budget,value", [("max_depth", 0), ("max_requests", 1)])
def test_bounds_are_enforced_even_with_dense_target_history(budget, value):
    v = Venue([row(i + 1, i, "TRANSFER", "1") for i in range(2000)])
    with pytest.raises(m.ContractBlocked):
        m.scan(v, 0, 3000, **{budget: value})


def test_slow_response_cannot_produce_success_after_time_budget():
    timer = [0.0]

    def reader(*args):
        timer[0] = 31.0
        return []

    with pytest.raises(m.ContractBlocked, match="TIME_BUDGET_EXHAUSTED"):
        m.scan(reader, 0, 100, clock=lambda: timer[0])


@pytest.mark.parametrize("value", [True, None, "-1", "1.5", "１２", 0])
def test_invalid_target_identity_is_rejected(value):
    with pytest.raises(m.ContractBlocked, match="INVALID_TRANSFER_ID"):
        m.scan(Venue([row(value, 1, "TRANSFER", "1")]), 0, 2)
