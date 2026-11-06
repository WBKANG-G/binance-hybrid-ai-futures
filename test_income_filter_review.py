"""Bounded income comparison: real isolated read-only PG, synthetic HTTP only."""

import importlib.util
import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from integrations.binance_usdm.demo import DemoRateLimited
from integrations.binance_usdm.demo_read_routing import TPReadRouting
from integrations.binance_usdm.rest import RESTResponse
from packages.persistence.demo import accounts, events
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_tp_rate_install_guard import closed  # noqa: F401

spec = importlib.util.spec_from_file_location(
    "income_filter_review", Path(__file__).with_name("compare_income_filters.py")
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def rows(store):
    with store.engine.connect() as c:
        return c.execute(select(accounts)).mappings().all(), c.execute(
            select(events)
        ).mappings().all()


@pytest.fixture
def setup(closed, monkeypatch):  # noqa: F811
    store, old, fake = closed
    monkeypatch.setattr(m, "ACCOUNT", "DEMO-test")
    fake.at += timedelta(seconds=60)
    now = int(fake.at.timestamp() * 1000)
    data = [
        {
            "incomeType": "TRANSFER",
            "tranId": 101,
            "asset": "USDT",
            "income": "12.5000",
            "time": now - 20000,
            "info": "PRIVATE",
        },
        {
            "incomeType": "WELCOME_BONUS",
            "tranId": 101,
            "asset": "USDT",
            "income": "1",
            "time": now - 19000,
        },
        {
            "incomeType": "COMMISSION",
            "tranId": 202,
            "asset": "USDT",
            "income": "-0.020",
            "time": now - 18000,
        },
    ]
    options = {
        "data": data,
        "weight": 100,
        "limit": 6000,
        "header": True,
        "ignore_filter": False,
        "drop_bonus": False,
        "change_second": False,
        "rate_call": None,
        "full": None,
        "income_calls": 0,
        "fail": False,
        "metadata": "valid",
        "empty_on_post": True,
    }
    moment = [0.0]
    calls = []
    sender = old.send

    def advance(sec):
        moment[0] += sec
        fake.at += timedelta(seconds=sec)

    def send(method, path, query, key):
        values = {k: v[0] for k, v in parse_qs(query.get_secret_value()).items()}
        calls.append(
            (
                method,
                path,
                {
                    k: v
                    for k, v in values.items()
                    if k not in {"signature", "timestamp", "recvWindow"}
                },
            )
        )
        advance(0.01)
        options["weight"] += {
            "/fapi/v1/income": 30,
            "/fapi/v1/openOrders": 40,
            "/fapi/v1/openAlgoOrders": 40,
            "/fapi/v3/positionRisk": 5,
        }.get(path, 1)
        if options["fail"] and path == "/fapi/v1/income":
            raise OSError("PRIVATE_TRANSPORT_ERROR")
        if path == "/fapi/v1/income":
            options["income_calls"] += 1
            if options.get("jump_at_income") == options["income_calls"]:
                options["weight"] = 4000
            if options.get("reset_at_income") == options["income_calls"]:
                options["weight"] = 30
            if options["rate_call"] == options["income_calls"]:
                return RESTResponse(
                    429,
                    b"not-json",
                    {"Retry-After": "120", "X-MBX-USED-WEIGHT-1M": str(options["weight"])},
                )
            kind = values.get("incomeType")
            if options.get("reject_bonus") and kind == "WELCOME_BONUS":
                return RESTResponse(
                    400,
                    b'{"code":-1100,"msg":"PRIVATE_SERVER_DETAILS"}',
                    {"X-MBX-USED-WEIGHT-1M": str(options["weight"])},
                )
            selected = [
                dict(r)
                for r in options["data"]
                if kind is None or options["ignore_filter"] or r["incomeType"] == kind
            ]
            if kind == "WELCOME_BONUS" and options["drop_bonus"]:
                selected = []
            if options["change_second"] and options["income_calls"] == 4:
                selected[0]["income"] = "13"
            if options.get("change_local") and options["income_calls"] == 4:
                with store.engine.begin() as conn:
                    conn.execute(
                        accounts.update()
                        .where(accounts.c.account_id == "DEMO-test")
                        .values(fence=accounts.c.fence + 1)
                    )
            if options["full"] == "all" or options["full"] == kind and kind is not None:
                selected = [dict(options["data"][0], tranId=i) for i in range(1000)]
            response = fake.response(selected)
        else:
            response = sender(method, path, query, key)
            if path == "/fapi/v1/exchangeInfo":
                raw = json.loads(response.body)
                raw["rateLimits"] = [
                    {
                        "rateLimitType": "REQUEST_WEIGHT",
                        "interval": "MINUTE",
                        "intervalNum": 1,
                        "limit": options["limit"],
                    }
                ]
                if options["metadata"] == "ambiguous":
                    raw["rateLimits"] *= 2
                if options["metadata"] == "missing":
                    raw.pop("rateLimits")
                response = fake.response(raw)
        if (
            options.get("missing_header_at_income") == options["income_calls"]
            and path == "/fapi/v1/income"
        ):
            options["header"] = False
        headers = {"X-MBX-USED-WEIGHT-1M": str(options["weight"])} if options["header"] else {}
        return RESTResponse(response.status, response.body, headers)

    class Pool:
        def __call__(self, *args):
            return send(*args)

        def close(self):
            pass

        def diagnostics(self):
            return {}

    route = TPReadRouting(send, pool=Pool())
    monitor = m.Monitor(route, clock=lambda: moment[0])
    client = m.IncomeCompareClient(
        old.settings, old.credentials, send=monitor, clock_ms=old.clock_ms
    )
    monitor.server_ms = lambda: client.clock_ms() + client.offset
    return store, client, monitor, options, calls, fake, advance, route


def test_fixed_window_global_query_match_has_no_database_or_credential_output(setup, monkeypatch):  # noqa: F811
    store, c, monitor, opt, calls, _, _, route = setup
    before = rows(store)
    for method in ("create", "acquire", "renew", "release", "change"):
        monkeypatch.setattr(
            store, method, lambda *a, **k: pytest.fail("probe cannot mutate or acquire")
        )
    r = m.compare(store, c, monitor)
    route.close()
    assert r["income_filter_review"] == "BOUNDED_WINDOW_MATCH" and r["both_target_types_observed"]
    assert r["transfer_rows"] == r["welcome_bonus_rows"] == r["other_income_rows"] == 1
    assert not r["optimization_approved"] and not r["order_writes"] and rows(store) == before
    income = [x for x in calls if x[1] == "/fapi/v1/income"]
    assert len(income) == 4 and [x[2].get("incomeType") for x in income] == [
        None,
        "TRANSFER",
        "WELCOME_BONUS",
        None,
    ]
    assert len({(x[2]["startTime"], x[2]["endTime"]) for x in income}) == 1
    assert all("page" not in x[2] and "symbol" not in x[2] and x[0] == "GET" for x in income)
    assert all(x[0] == "GET" for x in calls) and "PRIVATE" not in json.dumps(r)


def test_empty_results_are_a_match_without_positive_type_coverage(setup):  # noqa: F811
    store, c, monitor, opt, *_ = setup
    opt["data"] = []
    r = m.compare(store, c, monitor)
    assert (
        r["income_filter_review"] == "BOUNDED_WINDOW_MATCH" and not r["both_target_types_observed"]
    )
    assert r["event_type_coverage"] == "TARGET_TYPES_INCOMPLETE" and not r["optimization_approved"]


@pytest.mark.parametrize(
    "fault,reason",
    [
        ("ignore_filter", "FILTER_IGNORED_OR_WRONG_TYPE"),
        ("drop_bonus", "FILTERED_UNION_MISMATCH"),
        ("change_second", "UNFILTERED_WINDOW_CHANGED"),
        ("full", "INCOME_RESULT_AT_LIMIT_COVERAGE_UNPROVEN"),
    ],
)
def test_filter_or_history_ambiguity_blocks_without_any_change(setup, fault, reason):  # noqa: F811
    store, c, monitor, opt, calls, *_ = setup
    before = rows(store)
    opt[fault] = "all" if fault == "full" else True
    with pytest.raises(m.Blocked, match=reason):
        m.compare(store, c, monitor)
    assert rows(store) == before and all(x[0] == "GET" for x in calls)
    if fault == "full":
        assert opt["income_calls"] == 1


@pytest.mark.parametrize(
    "fault",
    ["duplicate", "missing_time", "future", "nonfinite", "bool_id", "other_asset", "bad_shape"],
)
def test_invalid_rows_never_approve_or_expose_private_values(setup, fault):  # noqa: F811
    store, c, monitor, opt, calls, *_ = setup
    before = rows(store)
    if fault == "duplicate":
        opt["data"].append(dict(opt["data"][0]))
    elif fault == "missing_time":
        opt["data"][0].pop("time")
    elif fault == "future":
        opt["data"][0]["time"] += 100000
    elif fault == "nonfinite":
        opt["data"][0]["income"] = "NaN"
    elif fault == "bool_id":
        opt["data"][0]["tranId"] = True
    elif fault == "other_asset":
        opt["data"][0]["asset"] = "BTC"
    else:
        opt["data"][0] = []
    with pytest.raises(Exception) as e:
        m.compare(store, c, monitor)
    assert "PRIVATE" not in m.safe_reason(e.value)
    assert rows(store) == before and all(x[0] == "GET" for x in calls)


@pytest.mark.parametrize("which", [1, 2, 3, 4])
def test_nonjson_429_stops_without_further_network_and_keeps_cooldown(setup, which):  # noqa: F811
    store, c, monitor, opt, calls, *_ = setup
    before = rows(store)
    opt["rate_call"] = which
    with pytest.raises(DemoRateLimited):
        m.compare(store, c, monitor)
    assert opt["income_calls"] == which and monitor.records[-1]["http_status"] == 429
    assert monitor.limited and c.cooldown_until > c.clock_ms() and rows(store) == before


@pytest.mark.parametrize("metadata", ["missing", "ambiguous"])
def test_bad_published_limit_stops_before_any_signed_read(setup, metadata):  # noqa: F811
    store, c, monitor, opt, calls, *_ = setup
    opt["metadata"] = metadata
    with pytest.raises(m.Blocked):
        m.compare(store, c, monitor)
    assert not any(r["signed"] for r in monitor.records)


def test_high_weight_stops_before_signed_reads(setup):  # noqa: F811
    store, c, monitor, opt, *_ = setup
    opt["weight"] = 3550
    with pytest.raises(m.ProbeStop, match="WEIGHT_HEADROOM_THRESHOLD"):
        m.compare(store, c, monitor)
    assert not any(r["signed"] for r in monitor.records)


def test_missing_header_does_not_allow_filter_queries(setup):  # noqa: F811
    store, c, monitor, opt, *_ = setup
    opt["header"] = False
    with pytest.raises(m.ProbeStop, match="WEIGHT_HEADER_UNAVAILABLE"):
        m.compare(store, c, monitor)
    assert not opt["income_calls"]


@pytest.mark.parametrize("budget", ["clock", "http", "time"])
def test_finite_start_budgets_stop_without_retry(setup, monkeypatch, budget):  # noqa: F811
    store, c, monitor, opt, calls, _, advance, _ = setup
    if budget == "clock":
        monkeypatch.setattr(m, "MAX_CLOCK_ATTEMPTS", 1)
    elif budget == "http":
        monkeypatch.setattr(m, "MAX_HTTP_ATTEMPTS", 2)
    else:
        advance(80)
    with pytest.raises(m.ProbeStop):
        m.compare(store, c, monitor)
    assert not opt["income_calls"]


def test_period_mismatch_is_reported_without_recovery_or_trading_approval(setup):  # noqa: F811
    store, c, monitor, opt, calls, fake, advance, _ = setup
    before = rows(store)
    fake.at += timedelta(days=1)
    # Income fixtures outside the selected local start are still valid in the
    # fixed24h query after a1-day advance only if no events are fabricated.
    opt["data"] = []
    r = m.compare(store, c, monitor)
    assert not r["period_keys_match"] and not r["optimization_approved"] and rows(store) == before


@pytest.mark.parametrize(
    "method,path,params",
    [
        ("POST", "/fapi/v1/order", {}),
        ("DELETE", "/fapi/v1/algoOrder", {}),
        ("GET", "/fapi/v3/account", {}),
        ("GET", "/fapi/v1/income", {"page": "1"}),
    ],
)
def test_client_scope_rejected_before_transport(setup, method, path, params):  # noqa: F811
    _, c, monitor, _, calls, *_ = setup
    with pytest.raises(m.Blocked):
        c.request(method, path, params)
    assert not calls


def test_monitor_forbids_unknown_and_mutation_even_if_client_guard_is_bypassed(setup):  # noqa: F811
    _, c, monitor, _, calls, *_ = setup
    for method, path in [("POST", "/fapi/v1/listenKey"), ("GET", "/fapi/v3/account")]:
        with pytest.raises(m.ProbeStop):
            monitor(method, path, SecretStr("PRIVATE"), SecretStr("PRIVATE"))
    with pytest.raises(m.ProbeStop):
        c.listen_key("POST")
    assert not calls


def test_source_pin_failure_precedes_database_and_keys(monkeypatch, capsys):
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    monkeypatch.setattr(m, "NEW_PINS", {"MISSING_SOURCE": "0" * 64})
    monkeypatch.setattr(m, "credentials", lambda **k: pytest.fail("no keys for unpinned sources"))
    monkeypatch.setattr(
        m, "create_engine", lambda *a, **k: pytest.fail("no DB for unpinned sources")
    )
    with pytest.raises(SystemExit) as e:
        m.main()
    assert (
        e.value.code == 2
        and json.loads(capsys.readouterr().out)["stop_reason"] == "INSTALLED_SOURCE_HASH_MISMATCH"
    )


def test_unknown_exception_text_is_redacted_and_cleanup_is_attempted(setup, monkeypatch, capsys):  # noqa: F811
    store, c, monitor, opt, calls, *_ = setup
    disposed = []
    monkeypatch.setattr(m, "verify_sources", lambda: None)
    monkeypatch.setattr(
        m, "create_engine", lambda *a, **k: SimpleNamespace(dispose=lambda: disposed.append(True))
    )
    monkeypatch.setattr(m, "DemoStore", lambda e: store)
    monkeypatch.setattr(
        m,
        "credentials",
        lambda **k: (_ for _ in ()).throw(ValueError("PRIVATE_CREDENTIAL_DETAILS")),
    )
    with pytest.raises(SystemExit):
        m.main()
    r = capsys.readouterr().out
    assert "PRIVATE" not in r and disposed
    assert m.safe_reason(m.Blocked("PRIVATE")) == "CHECK_UNAVAILABLE"


@pytest.mark.parametrize("which", [1, 2, 3, 4])
def test_header_loss_after_discovery_stops_immediately(setup, which):  # noqa: F811
    store, c, monitor, opt, calls, *_ = setup
    opt["missing_header_at_income"] = which
    with pytest.raises(m.ProbeStop, match="WEIGHT_HEADER_UNAVAILABLE"):
        m.compare(store, c, monitor)
    assert opt["income_calls"] == which and calls[-1][1] == "/fapi/v1/income"


def test_counter_reset_is_not_reported_as_a_negative_or_proven_cost(setup):  # noqa: F811
    store, c, monitor, opt, *_ = setup
    opt["reset_at_income"] = 2
    r = m.compare(store, c, monitor)
    assert r["scan_sequence"][1]["counter_reset_or_window_change"]
    assert r["scan_sequence"][1]["observed_counter_delta_sum"] is None
    assert r["observed_deltas_are_not_weight_proof"] and not r["optimization_approved"]


def test_high_shared_weight_response_stops_before_next_income_query(setup):  # noqa: F811
    store, c, monitor, opt, *_ = setup
    opt["jump_at_income"] = 1
    with pytest.raises(m.ProbeStop, match="WEIGHT_HEADROOM_THRESHOLD"):
        m.compare(store, c, monitor)
    assert opt["income_calls"] == 1


def test_current_nonzero_position_blocks_before_income_queries(setup):  # noqa: F811
    from decimal import Decimal

    store, c, monitor, opt, calls, fake, *_ = setup
    fake.positions["BTCUSDT"] = Decimal(".0007")
    fake.prices["BTCUSDT"] = Decimal("80000")
    with pytest.raises(m.Blocked, match="CURRENT_EXCHANGE_NOT_EMPTY"):
        m.compare(store, c, monitor)
    assert not opt["income_calls"] and all(row[0] == "GET" for row in calls)


@pytest.mark.parametrize("kind", ["TRANSFER", "WELCOME_BONUS"])
def test_one_observed_type_does_not_prove_both_type_contracts(setup, kind):  # noqa: F811
    store, c, monitor, opt, *_ = setup
    opt["data"] = [r for r in opt["data"] if r["incomeType"] == kind]
    r = m.compare(store, c, monitor)
    assert r["income_filter_review"] == "BOUNDED_WINDOW_MATCH"
    assert not r["both_target_types_observed"] and not r["optimization_approved"]


@pytest.mark.parametrize(
    "extra", [{"page": "1"}, {"symbol": "BTCUSDT"}, {"incomeType": "FUNDING_FEE"}]
)
def test_bound_window_still_forbids_paging_narrowing_and_other_filter_types(setup, extra):  # noqa: F811
    _, c, monitor, _, calls, *_ = setup
    c.window = 1, 2
    values = {"startTime": "1", "endTime": "2", "limit": "1000"} | extra
    with pytest.raises(m.Blocked, match="INCOME_QUERY_SCOPE_FORBIDDEN"):
        c.request("GET", "/fapi/v1/income", values)
    assert not calls


def test_live_context_refuses_before_pins_database_or_keys(monkeypatch, capsys):
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    monkeypatch.setattr(m, "verify_sources", lambda: pytest.fail("LIVE context must stop first"))
    with pytest.raises(SystemExit) as e:
        m.main()
    assert (
        e.value.code == 2
        and json.loads(capsys.readouterr().out)["stop_reason"] == "LIVE_CONTEXT_FORBIDDEN"
    )


@pytest.mark.parametrize("close_failure", [False, True])
def test_main_success_and_cleanup_failure_have_no_side_effect_or_private_output(
    setup, monkeypatch, capsys, close_failure
):  # noqa: F811
    store, c, monitor, opt, calls, _, _, route = setup
    before = rows(store)
    disposed = []

    def close():
        if close_failure:
            raise OSError("PRIVATE_CLOSE_DETAILS")

    monkeypatch.setattr(
        m, "create_engine", lambda *a, **k: SimpleNamespace(dispose=lambda: disposed.append(True))
    )
    monkeypatch.setattr(m, "DemoStore", lambda e: store)
    monkeypatch.setattr(m, "credentials", lambda **k: c.credentials)
    monkeypatch.setattr(m, "TPReadRouting", lambda sender: SimpleNamespace(close=close))
    monkeypatch.setattr(m, "Monitor", lambda route: monitor)
    monkeypatch.setattr(m, "IncomeCompareClient", lambda *a, **k: c)
    with pytest.raises(SystemExit) as e:
        m.main()
    raw = capsys.readouterr().out
    r = json.loads(raw)
    assert disposed and rows(store) == before and "PRIVATE" not in raw
    assert not r["optimization_approved"] and not r["order_writes"]
    if close_failure:
        assert e.value.code == 2 and r["stop_reason"] == "RESOURCE_CLEANUP_UNVERIFIED"
    else:
        assert e.value.code == 0 and r["income_filter_review"] == "BOUNDED_WINDOW_MATCH"


def test_filter_rejection_is_unverified_without_fallback_or_private_error_output(setup):  # noqa: F811
    store, c, monitor, opt, calls, *_ = setup
    opt["reject_bonus"] = True
    with pytest.raises(Exception) as e:
        m.compare(store, c, monitor)
    assert m.safe_reason(e.value) == "SIGNED_QUERY_OR_FILTER_SUPPORT_UNVERIFIED"
    assert opt["income_calls"] == 3 and calls[-1][1] == "/fapi/v1/income"
    assert "PRIVATE" not in m.safe_reason(e.value)


def test_external_fence_change_cannot_return_success_or_mutate_history(setup):  # noqa: F811
    store, c, monitor, opt, calls, *_ = setup
    before = rows(store)
    opt["change_local"] = True
    with pytest.raises(m.Blocked, match="LOCAL_STATE_CHANGED"):
        m.compare(store, c, monitor)
    after = rows(store)
    assert after[1] == before[1] and after[0][0]["state"] == before[0][0]["state"]
    assert after[0][0]["fence"] == before[0][0]["fence"] + 1
    assert all(call[0] == "GET" for call in calls)


@pytest.mark.parametrize("kind", ["INTERNAL_TRANSFER", "UNRECOGNIZED_PRIVATE_TYPE"])
def test_other_financial_types_are_visible_without_echoing_unrecognized_values(setup, kind):  # noqa: F811
    store, c, monitor, opt, *_ = setup
    opt["data"].append(dict(opt["data"][0], incomeType=kind, tranId=303))
    r = m.compare(store, c, monitor)
    assert r["target_types_are_current_ledger_logic_not_all_financial_classification"]
    assert not r["optimization_approved"]
    if kind == "INTERNAL_TRANSFER":
        assert r["non_target_type_counts"]["INTERNAL_TRANSFER"] == 1
    else:
        assert r["unknown_income_type_rows"] == 1
        assert "PRIVATE" not in json.dumps(r)
