"""A latched hard stop must precede shared reservations and order authorization."""

# ruff: noqa: F811
from urllib.parse import urlencode

import pytest
from pydantic import SecretStr

from integrations.binance_usdm.demo import DemoFailure
from integrations.binance_usdm.demo_tp_window_budget import weight_scope
from integrations.binance_usdm.rest import RESTResponse
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_managed_order_quota import managed  # noqa: F401
from tests.integration.test_managed_runtime_assembly import assembly  # noqa: F401
from tests.integration.test_quota_startup_review import snapshot
from tests.integration.test_runtime_header_provenance import prepared
from tests.unit.test_quota_startup_read_only import files

ROUTES = [
    ("GET", "/fapi/v1/time", {}),
    ("GET", "/fapi/v1/ticker/bookTicker", {"symbol": "BTCUSDT"}),
    ("GET", "/fapi/v3/positionRisk", {}),
    (
        "POST",
        "/fapi/v1/order",
        {
            "symbol": "BTCUSDT",
            "side": "BUY",
            "type": "LIMIT",
            "timeInForce": "GTC",
            "quantity": "0.0007",
            "price": "100",
            "newClientOrderId": "DE-test",
        },
    ),
    ("DELETE", "/fapi/v1/listenKey", {}),
]


def wire(method, path, params):
    if path in {"/fapi/v1/time", "/fapi/v1/ticker/bookTicker"}:
        return SecretStr(urlencode(params)), None
    if path == "/fapi/v1/listenKey":
        return SecretStr(""), SecretStr("synthetic-key")
    return SecretStr(
        urlencode(dict(params, timestamp="1000000", recvWindow="5000", signature="0" * 64))
    ), SecretStr("synthetic-key")


@pytest.mark.parametrize("lane", ["ORDINARY", "WATCH", "RESCUE", "PROOF"])
@pytest.mark.parametrize("method,path,params", ROUTES)
def test_latched_clock_stop_rejects_without_new_reservation_or_authorization(
    assembly, monkeypatch, lane, method, path, params
):
    calls = []

    def send(method, path, query, key):
        calls.append(path)
        return RESTResponse(200, b"{", {"x-mbx-used-weight-1m": "600"})

    session, b = prepared(assembly, send)
    c = session.coordinator.client
    c.clock_ms = lambda: 1_000_000
    try:
        with pytest.raises(DemoFailure):
            c.sync_clock()
        assert b.untrusted and b.stopped
        before = (
            snapshot(assembly[0]),
            files(assembly[8].order_directory),
            files(b.shared.directory),
        )
        weight = b.shared.read_only_snapshot()
        sent = list(calls)
        authorization = []
        original = b.bridge.authorize

        def authorize(*args, **kwargs):
            authorization.append(True)
            return original(*args, **kwargs)

        monkeypatch.setattr(b.bridge, "authorize", authorize)
        query, key = wire(method, path, params)
        with weight_scope(lane), pytest.raises(DemoFailure):
            b(method, path, query, key)
        assert snapshot(assembly[0]) == before[0]
        assert files(assembly[8].order_directory) == before[1]
        assert files(b.shared.directory) == before[2]
        assert b.shared.read_only_snapshot() == weight
        assert not authorization and calls == sent
        assert b.reason == "RUNTIME_CLOCK_REFRESH_FAILED"
    finally:
        session.close()


@pytest.mark.parametrize(
    "method,path,params,lane",
    [
        ("GET", "/fapi/v1/time", {}, "RESCUE"),
        ("GET", "/fapi/v1/time", {}, "WATCH"),
        ("DELETE", "/fapi/v1/listenKey", {}, "ORDINARY"),
    ],
)
def test_capacity_stop_keeps_original_rescue_and_close_key_paths(
    assembly, method, path, params, lane
):
    calls = []

    def send(method, path, query, key):
        calls.append(path)
        return RESTResponse(200, b"{}", {"x-mbx-used-weight-1m": "600"})

    session, b = prepared(assembly, send)
    try:
        b._mark("ROLLING_WEIGHT_RESERVE_REACHED")
        query, key = wire(method, path, params)
        with weight_scope(lane):
            result = b(method, path, query, key)
        assert result.status == 200 and calls[-1] == path and len(calls) == 2
        assert b.stopped and not b.untrusted and b.hard_stop_early_rejections == 0
        assert b.reason == "ROLLING_WEIGHT_RESERVE_REACHED"
    finally:
        session.close()


@pytest.mark.parametrize("fault", ["missing_header", "throttle", "transport"])
def test_prior_unknown_attempt_is_preserved_without_touching_reservation_provider(
    assembly, monkeypatch, fault
):
    import sqlite3

    calls = []

    def send(method, path, query, key):
        calls.append(path)
        if len(calls) == 1:
            return RESTResponse(200, b"{}", {"x-mbx-used-weight-1m": "600"})
        if fault == "transport":
            raise TimeoutError("synthetic clock transport loss")
        return RESTResponse(429 if fault == "throttle" else 200, b"{}", {})

    session, b = prepared(assembly, send)
    try:
        if fault == "transport":
            with pytest.raises(TimeoutError):
                b("GET", "/fapi/v1/time", SecretStr(""), None)
        else:
            b("GET", "/fapi/v1/time", SecretStr(""), None)
        assert b.untrusted and b.stopped
        disk = files(b.shared.directory)
        reason = b.reason
        touched = []

        def forbidden(*args, **kwargs):
            touched.append(True)
            raise AssertionError("stopped admission reached reservation provider")

        monkeypatch.setattr(b.shared, "reserve", forbidden)
        with weight_scope("RESCUE"), pytest.raises(DemoFailure):
            b("GET", "/fapi/v1/time", SecretStr(""), None)
        assert not touched and files(b.shared.directory) == disk and len(calls) == 2
        assert b.reason == reason
        with sqlite3.connect(b.shared.path.as_uri() + "?mode=ro", uri=True) as connection:
            assert connection.execute(
                "SELECT COUNT(*), SUM(weight) FROM attempts WHERE done IS NULL"
            ).fetchone() == (1, 1)
    finally:
        session.close()


def test_repeated_sdk_refresh_after_latch_has_no_new_persistent_cost_records(assembly):
    calls = []

    def send(method, path, query, key):
        calls.append(path)
        return RESTResponse(200, b"{", {"x-mbx-used-weight-1m": "600"})

    session, b = prepared(assembly, send)
    client = session.coordinator.client
    client.clock_ms = lambda: 1_000_000
    try:
        with pytest.raises(DemoFailure):
            client.sync_clock()
        disk, domain = files(b.shared.directory), snapshot(assembly[0])
        weight = b.shared.read_only_snapshot()
        for _ in range(3):
            with pytest.raises(DemoFailure):
                client.sync_clock()
        assert files(b.shared.directory) == disk and snapshot(assembly[0]) == domain
        assert b.shared.read_only_snapshot() == weight and len(calls) == 2
        assert b.hard_stop_early_rejections == 3 and b.total_charge == 2
        assert client.synced_at is None and client.clock_sample is None
        assert b.reason == "RUNTIME_CLOCK_REFRESH_FAILED"
    finally:
        session.close()
