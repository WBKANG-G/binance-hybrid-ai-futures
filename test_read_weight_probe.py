"""Read-only bounded profiler: real isolated PG, independent synthetic weights."""

import importlib.util
import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from integrations.binance_usdm.demo_read_routing import TPReadRouting
from integrations.binance_usdm.rest import RESTResponse
from packages.persistence.demo import accounts, events
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_tp_rate_install_guard import closed  # noqa: F401

spec = importlib.util.spec_from_file_location(
    "read_weight_probe", Path(__file__).with_name("probe_read_weight.py")
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


@pytest.fixture
def setup(closed, monkeypatch):  # noqa: F811
    store, client, fake = closed
    monkeypatch.setattr(m, "ACCOUNT", "DEMO-test")
    original = client.send
    options = {
        "weight": 100,
        "limit": 2400,
        "header": True,
        "rate": False,
        "metadata": "valid",
        "reset": False,
    }
    moment = [0.0]
    previous_window = [int(fake.at.timestamp()) // 60]

    def advance(seconds):
        moment[0] += seconds
        fake.at += timedelta(seconds=seconds)

    def send(method, path, query, key):
        advance(0.01)
        cost = {
            "/fapi/v1/openOrders": 40,
            "/fapi/v3/account": 5,
            "/fapi/v3/positionRisk": 5,
            "/fapi/v1/income": 30,
        }.get(path, 1)
        options["weight"] += cost
        window = int(fake.at.timestamp()) // 60
        if options["reset"] and window != previous_window[0]:
            options["weight"] = cost
        previous_window[0] = window
        if options["rate"] and path == "/fapi/v3/account":
            return RESTResponse(
                429,
                b"not-json",
                {"Retry-After": "120", "X-MBX-USED-WEIGHT-1M": str(options["weight"])},
            )
        result = original(method, path, query, key)
        if path == "/fapi/v1/exchangeInfo":
            raw = json.loads(result.body)
            if options["metadata"] == "valid":
                raw["rateLimits"] = [
                    {
                        "rateLimitType": "REQUEST_WEIGHT",
                        "interval": "MINUTE",
                        "intervalNum": 1,
                        "limit": options["limit"],
                    }
                ]
            elif options["metadata"] == "ambiguous":
                raw["rateLimits"] = [
                    {
                        "rateLimitType": "REQUEST_WEIGHT",
                        "interval": "MINUTE",
                        "intervalNum": 1,
                        "limit": 2400,
                    }
                ] * 2
            result = fake.response(raw)
        return RESTResponse(
            result.status,
            result.body,
            {"X-MBX-USED-WEIGHT-1M": str(options["weight"])} if options["header"] else {},
        )

    class Pool:
        def __call__(self, *args):
            return send(*args)

        def close(self):
            pass

        def diagnostics(self):
            return {}

    route = TPReadRouting(send, pool=Pool())
    monitor = m.Monitor(route, clock=lambda: moment[0])
    monitor.server_ms = lambda: client.clock_ms() + client.offset
    client.send = monitor
    # Any attempted DB mutation during the observation is a test failure.
    for name in ["acquire", "change", "renew", "release", "create"]:
        monkeypatch.setattr(
            store, name, lambda *args, **kwargs: pytest.fail("no DB writes or lease")
        )
    return store, client, fake, options, monitor, moment, advance, route


def rows(store):
    with store.engine.connect() as conn:
        return conn.execute(select(accounts)).mappings().all(), conn.execute(
            select(events)
        ).mappings().all()


def run(setup):
    store, client, _, _, monitor, moment, advance, route = setup
    before = rows(store)
    try:
        result = m.observe(store, client, monitor, clock=lambda: moment[0], wait=advance)
    finally:
        route.close()
    assert rows(store) == before
    assert (
        result["local_state_unchanged"]
        and not result["database_changed_by_probe"]
        and result["order_writes"] == 0
    )
    assert all(r["path"] in m.GET_PATHS | {"/fapi/v1/exchangeInfo"} for r in monitor.records)
    return result


def test_three_idle_bursts_only_read_and_keep_receipt_ledger_and_audit_rows(setup):
    result = run(setup)
    assert result["read_weight_probe"] == "READ_ONLY_OBSERVATION_FINISHED", result
    assert result["completed_rounds"] == 3 and result["request_weight_limit_1m"] == 2400
    assert [r["started_after_discovery_ms"] for r in result["rounds"]] == [0, 15000, 30000]
    assert all(
        r["operational_snapshots"] == 3 and r["remote_empty_verified"] for r in result["rounds"]
    )
    assert result["http_attempts"] < 160 and result["elapsed_ms"] < 45000
    assert (
        result["counter_deltas_include_other_ip_activity"]
        and not result["active_position_or_private_stream_load_measured"]
    )
    assert result["next_action"] == "REVIEW_ONLY_NO_ORDER_FROM_THIS_REPORT"


@pytest.mark.parametrize(
    "fault",
    ["headroom", "missing_header", "missing_limit", "ambiguous_limit", "rate", "time", "count"],
)
def test_observation_stops_without_retry_or_any_mutation(setup, monkeypatch, fault):
    _, _, _, options, monitor, _, _, _ = setup
    if fault == "headroom":
        options["weight"] = 1400
    elif fault == "missing_header":
        options["header"] = False
    elif fault == "missing_limit":
        options["metadata"] = "missing"
    elif fault == "ambiguous_limit":
        options["metadata"] = "ambiguous"
    elif fault == "rate":
        options["rate"] = True
    elif fault == "time":
        monkeypatch.setattr(m, "MAX_SECONDS", 1)
    elif fault == "count":
        monkeypatch.setattr(m, "MAX_HTTP_ATTEMPTS", 5)
    result = run(setup)
    assert result["read_weight_probe"] != "READ_ONLY_OBSERVATION_FINISHED"
    assert result["completed_rounds"] < 3
    if fault in {"headroom", "missing_header", "missing_limit", "ambiguous_limit"}:
        assert not result["signed_gets"]
    if fault == "rate":
        assert result["rate_limit_diagnostic"]["http_rate_limit_status"] == 429
        limited = [r for r in monitor.records if r["http_status"] == 429]
        assert len(limited) == 1 and monitor.records[-1] is limited[0]


def test_counter_reset_never_becomes_negative_or_false_increment(setup):
    _, _, fake, options, _, _, advance, _ = setup
    fake.at += timedelta(seconds=50 - fake.at.second)
    options["reset"] = True
    result = run(setup)
    resets = [r for r in result["request_samples"] if r["counter_reset_or_window_change"]]
    assert resets and all(r["counter_delta"] is None for r in resets)
    assert all(
        r["counter_delta"] is None or r["counter_delta"] >= 0 for r in result["request_samples"]
    )


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/fapi/v1/order"),
        ("DELETE", "/fapi/v1/algoOrder"),
        ("PUT", "/fapi/v1/listenKey"),
        ("GET", "/fapi/v1/trade/asyn"),
        ("GET", "/private"),
    ],
)
def test_lowest_probe_boundary_forbids_all_writes_exports_and_unknown_routes(method, path):
    monitor = m.Monitor(lambda *args: pytest.fail("forbidden route must never send"))
    with pytest.raises(m.ProbeStop):
        monitor(method, path, SecretStr("PRIVATE"), None)
    assert not monitor.records


@pytest.mark.parametrize("limit", [True, 0, -1, "2400", 2400.0])
def test_published_limit_requires_one_exact_positive_integer(limit):
    info = {
        "rateLimits": [
            {
                "rateLimitType": "REQUEST_WEIGHT",
                "interval": "MINUTE",
                "intervalNum": 1,
                "limit": limit,
            }
        ]
    }
    with pytest.raises(m.Blocked):
        m.published_limit(info)


def test_installed_source_pins_match_and_clock_cap_stops_before_send():
    m.verify_sources()
    monitor = m.Monitor(lambda *args: RESTResponse(200, b"{}", {}))
    monitor.clock_attempts = m.MAX_CLOCK_ATTEMPTS
    with pytest.raises(m.ProbeStop, match="CLOCK_BUDGET"):
        monitor("GET", "/fapi/v1/time", SecretStr(""), None)
    assert not monitor.records


def test_cleanup_failure_is_redacted_and_engine_still_disposed(monkeypatch, capsys):
    disposed = []

    class Route:
        def __init__(self, *args):
            pass

        def close(self):
            raise RuntimeError("PRIVATE_CLOSE_DATA")

    monkeypatch.setattr(m, "TPReadRouting", Route)
    monkeypatch.setattr(
        m,
        "create_engine",
        lambda *args, **kwargs: SimpleNamespace(dispose=lambda: disposed.append(True)),
    )
    monkeypatch.setattr(m, "DemoStore", lambda engine: None)
    monkeypatch.setattr(m, "local", lambda store: None)
    monkeypatch.setattr(m, "credentials", lambda **kwargs: None)
    monkeypatch.setattr(
        m, "DemoClient", lambda *args, **kwargs: SimpleNamespace(clock_ms=lambda: 0, offset=0)
    )
    monkeypatch.setattr(
        m, "observe", lambda *args: {"read_weight_probe": "READ_ONLY_OBSERVATION_FINISHED"}
    )
    with pytest.raises(SystemExit) as code:
        m.probe_main()
    text = capsys.readouterr().out
    assert code.value.code == 2 and "PRIVATE_CLOSE_DATA" not in text and disposed == [True]
    assert json.loads(text)["stop_reason"] == "RESOURCE_CLEANUP_UNVERIFIED"


def test_main_rejects_wrong_installed_sources_before_credentials(monkeypatch, capsys):
    monkeypatch.setattr(m, "NEW_PINS", {"missing-source.py": "bad"})
    monkeypatch.setattr(m, "credentials", lambda **kwargs: pytest.fail("no keys on wrong source"))
    with pytest.raises(SystemExit) as code:
        m.probe_main()
    assert code.value.code == 2 and "INSTALLED_SOURCE_HASH_MISMATCH" in capsys.readouterr().out


def test_compact_output_retains_reset_and_shared_activity_limits_without_raw_samples():
    report = {
        "read_weight_probe": "READ_ONLY_OBSERVATION_FINISHED",
        "counter_deltas_include_other_ip_activity": True,
        "request_samples": [
            {
                "path": "/fapi/v1/time",
                "counter_delta": None,
                "counter_reset_or_window_change": True,
            },
            {"path": "/fapi/v1/time", "counter_delta": 3, "counter_reset_or_window_change": False},
        ],
    }
    result = m.compact_report(report)
    assert "request_samples" not in result and "request_samples" in report
    assert result["counter_reset_or_window_change_count"] == 1
    assert result["response_groups"] == [
        {
            "path": "/fapi/v1/time",
            "http_attempts": 2,
            "observed_delta_min": 3,
            "observed_delta_max": 3,
        }
    ]
    assert result["observed_deltas_are_not_endpoint_weight_proof"]


def test_slow_round_does_not_catch_up_with_shorter_burst_intervals(setup, monkeypatch):
    advance = setup[6]
    original = m.operational_snapshot
    calls = []

    def slow(*args):
        value = original(*args)
        calls.append(1)
        if len(calls) == 3:
            advance(20)
        return value

    monkeypatch.setattr(m, "operational_snapshot", slow)
    report = run(setup)
    assert report["completed_rounds"] == 3, report
    starts = [row["started_after_discovery_ms"] for row in report["rounds"]]
    assert all(b - a >= 14999 for a, b in zip(starts, starts[1:], strict=False))
