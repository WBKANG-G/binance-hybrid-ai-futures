import json

import pytest

from integrations.binance_usdm.rest import (
    BinanceREST,
    ExchangeUnavailable,
    RateLimited,
    RESTResponse,
)


def test_market_get_allowlist_and_no_secret_or_write_requests():
    calls = []

    def transport(path, params):
        calls.append((path, params))
        return RESTResponse(200, b'{"serverTime":1800000000000}', {})

    client = BinanceREST(transport=transport)
    assert client.get("/fapi/v1/time")["serverTime"] == 1800000000000
    for path in [
        "/fapi/v1/order",
        "https://attacker.invalid",
        "/fapi/v1/listenKey",
        "/fapi/v1/algoOrder",
    ]:
        with pytest.raises(ValueError):
            client.get(path)
    with pytest.raises(ValueError):
        client.get("/fapi/v1/time", {"signature": "secret"})
    assert len(calls) == 1


def test_exchange_error_rate_limit_and_bad_json_are_sanitized():
    for response, error in [
        (RESTResponse(429, b"api-key-sensitive", {"Retry-After": "2"}), RateLimited),
        (RESTResponse(451, b"api-key-sensitive", {}), ExchangeUnavailable),
        (RESTResponse(200, b"not-json-sensitive", {}), ExchangeUnavailable),
    ]:
        with pytest.raises(error) as caught:
            BinanceREST(transport=lambda *args, response=response: response).get("/fapi/v1/time")
        assert "sensitive" not in str(caught.value)


def test_decimal_json_parsing_and_bounded_historical_pagination():
    pages = []

    def transport(path, params):
        pages.append(params)
        start = int(params["startTime"])
        rows = [[start, "100", "101", "99", "100", "1", start + 59999, "100", 1, "0", "0", "0"]]
        return RESTResponse(200, json.dumps(rows).encode(), {})

    data = BinanceREST(transport=transport).historical_klines(
        "BTCUSDT", "1m", start_ms=1800000000000, end_ms=1800000120000, page_size=1
    )
    assert len(data) == 2 and len(pages) == 2
    with pytest.raises(ValueError):
        BinanceREST(transport=transport).historical_klines(
            "BTCUSDT", "1m", start_ms=0, end_ms=1, max_pages=0
        )


def test_clock_sync_rejects_unmeasured_stale_and_slow_samples():
    from integrations.binance_usdm.rest import ServerClock

    clock = ServerClock()
    with pytest.raises(ValueError):
        clock.timestamp(1000)
    with pytest.raises(ValueError):
        clock.synchronize(2000, sent_ms=0, received_ms=1001)
    clock.synchronize(2000, sent_ms=900, received_ms=1100)
    assert clock.timestamp(1000) == 2000
    clock.synced_at -= 61
    with pytest.raises(ValueError):
        clock.timestamp(1000)


@pytest.mark.parametrize("offset,empty", [(60000, False), (0, True)])
def test_missing_historical_bars_fail_instead_of_silently_shortening_range(offset, empty):
    def transport(path, params):
        start = int(params["startTime"]) + offset
        rows = (
            []
            if empty
            else [[start, "100", "101", "99", "100", "1", start + 59999, "100", 1, "0", "0", "0"]]
        )
        return RESTResponse(200, json.dumps(rows).encode(), {})

    with pytest.raises(ExchangeUnavailable):
        BinanceREST(transport).historical_klines(
            "BTCUSDT", "1m", start_ms=1800000000000, end_ms=1800000120000, page_size=1
        )


def test_success_status_with_api_error_or_invalid_clock_is_not_accepted():
    for payload in [{"code": -1000, "msg": "sensitive"}, {"serverTime": True}]:
        client = BinanceREST(
            lambda *args, payload=payload: RESTResponse(200, json.dumps(payload).encode(), {})
        )
        with pytest.raises(ExchangeUnavailable) as error:
            client.get("/fapi/v1/time")
        assert "sensitive" not in str(error.value)
