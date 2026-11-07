"""Routing/serialization tests, never network or live account access."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import SecretStr

from integrations.binance_usdm.demo import DemoFailure, DemoUnknown
from integrations.binance_usdm.demo_read_routing import TPReadRouting
from integrations.binance_usdm.rest import RESTResponse


class FakePool:
    def __init__(self):
        self.calls = []
        self.closes = 0
        self.error = False

    def __call__(self, *args):
        self.calls.append(args)
        if self.error:
            raise DemoUnknown("SYNTHETIC_FAILURE")
        return RESTResponse(200, b"{}", {})

    def close(self):
        self.closes += 1

    def diagnostics(self):
        return {
            "audit_transport_mode": "POOLED_HTTPS",
            "audit_http_attempts": len(self.calls),
            "PRIVATE_HEADER": "DO_NOT_RETURN",
        }


def setup():
    calls = []

    def original(*args):
        calls.append(args)
        return RESTResponse(200, b"{}", {})

    pool = FakePool()
    return TPReadRouting(original, pool=pool), pool, calls


@pytest.mark.parametrize(
    "path",
    [
        "/fapi/v1/time",
        "/fapi/v3/account",
        "/fapi/v3/positionRisk",
        "/fapi/v1/order",
        "/fapi/v1/algoOrder",
    ],
)
def test_allowed_gets_use_pool_once_with_exact_secret_objects(path):
    route, pool, calls = setup()
    query, key = SecretStr("SIGNED_QUERY_EXAMPLE"), SecretStr("SYNTHETIC_KEY")
    route("GET", path, query, key)
    assert len(pool.calls) == 1 and calls == []
    assert pool.calls[0] == ("GET", path, query, key)
    assert pool.calls[0][2] is query and pool.calls[0][3] is key
    route.close()


@pytest.mark.parametrize(
    "path",
    [
        "/fapi/v1/exchangeInfo",
        "/fapi/v1/ticker/bookTicker",
        "/fapi/v1/premiumIndex",
        "/fapi/v1/klines",
    ],
)
def test_market_data_gets_retain_original_transport(path):
    route, pool, calls = setup()
    query = SecretStr("symbol=BTCUSDT")
    route("GET", path, query, None)
    assert calls == [("GET", path, query, None)] and not pool.calls
    route.close()


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/fapi/v1/order"),
        ("POST", "/fapi/v1/algoOrder"),
        ("DELETE", "/fapi/v1/algoOrder"),
        ("PUT", "/fapi/v1/listenKey"),
        ("DELETE", "/fapi/v1/listenKey"),
    ],
)
def test_mutations_retain_original_one_shot_transport(method, path):
    route, pool, calls = setup()
    query, key = SecretStr("PAYLOAD_EXAMPLE"), SecretStr("SYNTHETIC_KEY")
    route(method, path, query, key)
    assert calls == [(method, path, query, key)] and not pool.calls
    assert calls[0][2] is query and calls[0][3] is key
    route.close()


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/fapi/v1/trade/asyn"),
        ("GET", "/fapi/v1/trade/asyn/id"),
        ("GET", "/unknown"),
        ("PATCH", "/fapi/v1/order"),
    ],
)
def test_exports_unknown_routes_and_methods_block_before_either_transport(method, path):
    route, pool, calls = setup()
    with pytest.raises(DemoFailure):
        route(method, path, SecretStr(""), None)
    assert not calls and not pool.calls
    route.close()


def test_failed_pooled_get_has_no_retry_or_original_fallback():
    route, pool, calls = setup()
    pool.error = True
    with pytest.raises(DemoUnknown):
        route("GET", "/fapi/v1/order", SecretStr(""), None)
    assert len(pool.calls) == 1 and not calls
    route.close()


def test_unknown_write_not_retried_and_not_routed_to_pool():
    calls = []

    def unknown(*args):
        calls.append(args)
        raise DemoUnknown("UNKNOWN_WRITE_EXAMPLE")

    pool = FakePool()
    route = TPReadRouting(unknown, pool=pool)
    with pytest.raises(DemoUnknown):
        route("POST", "/fapi/v1/order", SecretStr(""), None)
    assert len(calls) == 1 and not pool.calls
    route.close()


def test_concurrent_gets_never_share_an_active_connection_and_close_waits():
    entered, release = threading.Event(), threading.Event()

    class Blocking(FakePool):
        active = peak = 0

        def __call__(self, *args):
            self.active += 1
            self.peak = max(self.peak, self.active)
            entered.set()
            assert release.wait(5)
            try:
                return super().__call__(*args)
            finally:
                self.active -= 1

        def close(self):
            assert self.active == 0
            super().close()

    pool = Blocking()
    route = TPReadRouting(lambda *_: pytest.fail("not original"), pool=pool)
    with ThreadPoolExecutor(max_workers=3) as workers:
        first = workers.submit(route, "GET", "/fapi/v1/time", SecretStr(""), None)
        assert entered.wait(5)
        second = workers.submit(route, "GET", "/fapi/v1/order", SecretStr(""), None)
        time.sleep(0.02)
        assert not second.done() and len(pool.calls) == 0
        release.set()
        first.result(5)
        second.result(5)
        workers.submit(route.close).result(5)
    assert pool.peak == 1 and len(pool.calls) == 2 and pool.closes == 1


def test_close_is_idempotent_and_subsequent_calls_block_without_secret_diagnostics():
    route, pool, calls = setup()
    route.close()
    route.close()
    for method in ("GET", "POST"):
        with pytest.raises(DemoFailure):
            route(method, "/fapi/v1/order", SecretStr(""), None)
    report = route.diagnostics()
    assert pool.closes == 1 and report["closed"] and not calls and not pool.calls
    assert "DO_NOT_RETURN" not in str(report) and report["added_request_retries"] == 0


def test_close_waits_for_an_inflight_pooled_request():
    entered, release, closing = threading.Event(), threading.Event(), threading.Event()

    class Blocking(FakePool):
        active = False

        def __call__(self, *args):
            self.active = True
            entered.set()
            assert release.wait(5)
            try:
                return super().__call__(*args)
            finally:
                self.active = False

        def close(self):
            assert not self.active
            super().close()

    pool = Blocking()
    route = TPReadRouting(lambda *_: pytest.fail("not original"), pool=pool)

    def close():
        closing.set()
        route.close()

    with ThreadPoolExecutor(max_workers=2) as workers:
        request = workers.submit(route, "GET", "/fapi/v1/time", SecretStr(""), None)
        assert entered.wait(5)
        cleanup = workers.submit(close)
        assert closing.wait(5)
        time.sleep(0.02)
        assert not cleanup.done()
        release.set()
        request.result(5)
        cleanup.result(5)
    assert pool.closes == 1
