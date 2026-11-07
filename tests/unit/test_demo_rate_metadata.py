"""Response metadata/cooldown safety, synthetic inputs only."""

from datetime import timedelta
from email.utils import format_datetime
from io import BytesIO
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest
from pydantic import SecretStr

from integrations.binance_usdm import demo, recovery_transport
from integrations.binance_usdm.demo import DemoFailure, DemoRateLimited
from integrations.binance_usdm.demo_rate_limits import rate_headers, retry_delay_ms
from integrations.binance_usdm.rest import RESTResponse
from tests.unit.test_demo_transport import NOW, client


@pytest.mark.parametrize("status", [418, 429])
@pytest.mark.parametrize("body", [b'{"code":-1003}', b"<html>rate limit</html>", b"not-json"])
def test_rate_limit_precedes_json_decode_and_blocks_every_network_path(status, body):
    c, fake = client()
    c.send = lambda *args: RESTResponse(
        status,
        body,
        {"Retry-After": "120", "X-MBX-USED-WEIGHT-1M": "2300", "Authorization": "PRIVATE"},
    )
    with pytest.raises(DemoRateLimited):
        c.request("GET", "/fapi/v3/account")
    assert c.cooldown_until == c.clock_ms() + 120000
    diagnostic = c.rate_limit_diagnostics()
    assert (
        diagnostic["http_rate_limit_status"] == status
        and diagnostic["observed_max_used_weight_1m"] == 2300
    )
    assert "PRIVATE" not in str(diagnostic)
    c.send = lambda *args: pytest.fail("no network in cooldown")
    for action in [
        c.sync_clock,
        lambda: c.public("/fapi/v1/exchangeInfo"),
        lambda: c.listen_key("DELETE"),
        lambda: c.request("POST", "/fapi/v1/order", {"type": "LIMIT"}),
        lambda: c.request("GET", "/fapi/v3/account"),
    ]:
        with pytest.raises(DemoFailure):
            action()
    fake.at += timedelta(seconds=121)
    c.send = fake
    c.sync_clock()
    assert c.request("GET", "/fapi/v3/account")


@pytest.mark.parametrize(
    "value,minimum",
    [
        ("120", 120000),
        ("1", 60000),
        ("bad-secret", 60000),
        ("-2", 60000),
        ("999999999999", 999999999999000),
    ],
)
def test_retry_after_never_shortens_existing_minimum_or_active_cooldown(value, minimum):
    c, _ = client()
    c._decode(RESTResponse(200, b"{}", {"X-MBX-USED-WEIGHT-1M": "9"}), mutation=False)
    with pytest.raises(DemoRateLimited):
        c._decode(RESTResponse(429, b"{}", {"Retry-After": value}), mutation=False)
    assert c.cooldown_until >= c.clock_ms() + minimum
    previous = c.cooldown_until
    with pytest.raises(DemoRateLimited):
        c._decode(RESTResponse(418, b"{}", {"Retry-After": "1"}), mutation=False)
    assert c.cooldown_until >= previous


def test_http_date_and_header_whitelist():
    stamp = format_datetime(NOW + timedelta(minutes=3), usegmt=True)
    assert retry_delay_ms(stamp, int(NOW.timestamp() * 1000)) == 181000
    assert rate_headers(
        {
            "Retry-After": stamp,
            "x-mbx-used-weight-1m": "42",
            "Cookie": "PRIVATE",
            "X-MBX-APIKEY": "PRIVATE",
            "x-mbx-used-weight-1m-bad": "PRIVATE",
        }
    ) == {"retry-after": stamp, "x-mbx-used-weight-1m": "42"}
    assert rate_headers({"Retry-After": "PRIVATE", "X-MBX-USED-WEIGHT-1M": "PRIVATE"}) == {}
    assert retry_delay_ms("9" * 129, 0) is None


@pytest.mark.parametrize("pooled", [False, True])
@pytest.mark.parametrize("status", [200, 429])
def test_both_transports_preserve_only_safe_headers_and_close_errors(monkeypatch, pooled, status):
    metadata = {"Retry-After": "120", "X-MBX-USED-WEIGHT-1M": "2300", "Set-Cookie": "PRIVATE"}
    stream = BytesIO(b"{}")

    class Reply:
        headers = metadata

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, *args):
            return b"{}"

    Reply.status = status

    def open_request(*args, **kwargs):
        if status == 429:
            raise HTTPError("PRIVATE_SIGNED_URL", 429, "rate", metadata, stream)
        return Reply()

    if pooled:
        sender = recovery_transport.RecoveryTransport()
        monkeypatch.setattr(sender, "opener", SimpleNamespace(open=open_request))
        response = sender("GET", "/fapi/v1/time", SecretStr(""), None)
        sender.close()
    else:
        monkeypatch.setattr(demo, "build_opener", lambda *args: SimpleNamespace(open=open_request))
        response = demo.transport("GET", "/fapi/v1/time", SecretStr(""), None)
    assert response.headers == {"retry-after": "120", "x-mbx-used-weight-1m": "2300"}
    assert "PRIVATE" not in str(response.headers)
    if status == 429:
        assert stream.closed


def test_concurrent_responses_preserve_longest_cooldown_max_weight_and_all_samples():
    from concurrent.futures import ThreadPoolExecutor

    c, _ = client()

    def decode(n):
        with pytest.raises(DemoRateLimited):
            c._decode(
                RESTResponse(
                    429,
                    b"not-json",
                    {"Retry-After": str(60 + n), "X-MBX-USED-WEIGHT-1M": str(100 + n)},
                ),
                mutation=False,
            )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(decode, range(40)))
    assert c.cooldown_until == c.clock_ms() + 99000
    assert c.rate_limit_diagnostics()["observed_max_used_weight_1m"] == 139
    assert c.rate_limit_diagnostics()["weight_header_samples"] == 40
