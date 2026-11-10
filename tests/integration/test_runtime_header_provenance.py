"""Non-authoritative quote headers must not replace trustworthy observations."""

# ruff: noqa: F811
import pytest
from pydantic import SecretStr

from integrations.binance_usdm.rest import RESTResponse
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_managed_order_quota import managed  # noqa: F401
from tests.integration.test_managed_runtime_assembly import assembly, open_session  # noqa: F401


@pytest.mark.parametrize("header", ["0", "999999", None, "invalid", "-1"])
def test_quote_header_cannot_refresh_or_replace_previous_weight_observation(assembly, header):
    store, c, q, clock, w, source, sender, calls, storage, clocks = assembly
    original_headers = {} if header is None else {"x-mbx-used-weight-1m": header}
    quote = RESTResponse(200, b"{}", original_headers)

    def send(method, path, query, key):
        return (
            quote
            if path == "/fapi/v1/ticker/bookTicker"
            else RESTResponse(200, b"{}", {"x-mbx-used-weight-1m": "600"})
        )

    args = list(assembly)
    args[6] = send
    session = open_session(args, allow_synthetic_model_execution=True)
    b = session.budget
    b.clock = clock.mono
    b("GET", "/fapi/v1/time", SecretStr(""), None)
    b.configure(
        {
            "rateLimits": [
                {
                    "rateLimitType": "REQUEST_WEIGHT",
                    "interval": "MINUTE",
                    "intervalNum": 1,
                    "limit": 6000,
                },
                {"rateLimitType": "ORDERS", "interval": "SECOND", "intervalNum": 10, "limit": 300},
                {"rateLimitType": "ORDERS", "interval": "MINUTE", "intervalNum": 1, "limit": 1200},
            ]
        }
    )
    b.preflight()
    previous = (b.header_weight, b.header_charge, b.header_at, b.header_sequence)
    clock.seconds += 1
    try:
        result = b("GET", "/fapi/v1/ticker/bookTicker", SecretStr("symbol=BTCUSDT"), None)
        assert result is quote and result.headers is original_headers
        assert (b.header_weight, b.header_charge, b.header_at, b.header_sequence) == previous
        assert not b.untrusted and not b.stopped
        assert b.total_charge == 3
        assert b.shared.read_only_snapshot()["pressure"] == 602
    finally:
        session.close()


def prepared(assembly, send):
    args = list(assembly)
    args[6] = send
    session = open_session(args, allow_synthetic_model_execution=True)
    b = session.budget
    b.clock = assembly[3].mono
    b("GET", "/fapi/v1/time", SecretStr(""), None)
    b.configure(
        {
            "rateLimits": [
                {
                    "rateLimitType": "REQUEST_WEIGHT",
                    "interval": "MINUTE",
                    "intervalNum": 1,
                    "limit": 6000,
                },
                {"rateLimitType": "ORDERS", "interval": "SECOND", "intervalNum": 10, "limit": 300},
                {"rateLimitType": "ORDERS", "interval": "MINUTE", "intervalNum": 1, "limit": 1200},
            ]
        }
    )
    b.preflight()
    return session, b


def test_book_response_does_not_refresh_expired_metadata(assembly):
    clock = assembly[3]

    def send(method, path, query, key):
        if path.endswith("bookTicker"):
            clock.seconds += 0.2
        return RESTResponse(200, b"{}", {"x-mbx-used-weight-1m": "600"})

    session, b = prepared(assembly, send)
    try:
        previous = b.header_at
        clock.seconds += 4.9
        b("GET", "/fapi/v1/ticker/bookTicker", SecretStr("symbol=BTCUSDT"), None)
        assert b.header_at == previous
        assert not b._fresh(clock.mono())
        assert not b.untrusted
    finally:
        session.close()


@pytest.mark.parametrize("status", [418, 429])
@pytest.mark.parametrize("headers", [{}, {"x-mbx-used-weight-1m": "0"}])
def test_book_throttle_still_stops_local_and_shared_admission(assembly, status, headers):
    from packages.persistence.demo_shared_weight import SharedWeightStopped

    response = RESTResponse(status, b"{}", headers)

    def send(method, path, query, key):
        return (
            response
            if path.endswith("bookTicker")
            else RESTResponse(200, b"{}", {"x-mbx-used-weight-1m": "600"})
        )

    session, b = prepared(assembly, send)
    try:
        assert b("GET", "/fapi/v1/ticker/bookTicker", SecretStr("symbol=BTCUSDT"), None) is response
        assert b.untrusted and b.stopped
        with pytest.raises(SharedWeightStopped):
            b.shared.read_only_snapshot()
    finally:
        session.close()


def test_book_transport_failure_is_not_recovered(assembly):
    from packages.persistence.demo_shared_weight import SharedWeightStopped

    def send(method, path, query, key):
        if path.endswith("bookTicker"):
            raise TimeoutError("synthetic timeout")
        return RESTResponse(200, b"{}", {"x-mbx-used-weight-1m": "600"})

    session, b = prepared(assembly, send)
    try:
        with pytest.raises(TimeoutError):
            b("GET", "/fapi/v1/ticker/bookTicker", SecretStr("symbol=BTCUSDT"), None)
        assert b.untrusted and b.stopped
        with pytest.raises(SharedWeightStopped):
            b.shared.read_only_snapshot()
    finally:
        session.close()


def test_nested_trustworthy_observation_survives_book_response(assembly):
    def send(method, path, query, key):
        if path.endswith("bookTicker"):
            b("GET", "/fapi/v1/time", SecretStr(""), None)
            return RESTResponse(200, b"{}", {})
        return RESTResponse(200, b"{}", {"x-mbx-used-weight-1m": "700"})

    session, b = prepared(assembly, send)
    try:
        old_sequence = b.header_sequence
        assembly[3].seconds += 1
        b("GET", "/fapi/v1/ticker/bookTicker", SecretStr("symbol=BTCUSDT"), None)
        assert b.header_sequence == old_sequence + 2
        assert b.header_weight == 700 and b.header_at == assembly[3].mono()
        assert not b.untrusted and not b.stopped
    finally:
        session.close()


def test_other_route_missing_header_remains_a_fault(assembly):
    count = 0

    def send(method, path, query, key):
        nonlocal count
        count += 1
        return RESTResponse(200, b"{}", {"x-mbx-used-weight-1m": "600"} if count == 1 else {})

    session, b = prepared(assembly, send)
    try:
        b("GET", "/fapi/v1/time", SecretStr(""), None)
        assert b.untrusted and b.stopped
        assert b.ignored_quote_observations == 0
    finally:
        session.close()
