"""DEMO isolation/signing/uncertainty contracts with synthetic credentials only."""

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs

import pytest
from pydantic import SecretStr

from integrations.binance_usdm.demo import (
    REST_HOST,
    WS_HOST,
    DemoClient,
    DemoCredentials,
    DemoFailure,
    DemoNotFound,
    DemoRateLimited,
    DemoRejected,
    DemoSettings,
    DemoUnknown,
)
from integrations.binance_usdm.orders import OrderIntent
from integrations.binance_usdm.rest import RESTResponse, sign_payload
from tests.fixtures.demo import FakeDemo
from tests.integration.test_demo import settings

NOW = datetime(2026, 10, 19, 7, tzinfo=UTC)


def client(enabled=True):
    config = DemoSettings.model_validate(
        settings().model_dump() | {"demo_trading_enabled": enabled}
    )
    fake = FakeDemo(NOW)
    result = DemoClient(
        config,
        DemoCredentials(SecretStr("synthetic-key"), SecretStr("synthetic-secret")),
        send=fake,
        clock_ms=lambda: int(fake.at.timestamp() * 1000),
    )
    result.sync_clock()
    return result, fake


def test_demo_host_and_credentials_are_isolated_and_masked(monkeypatch):
    for name in [
        "BINANCE_API_KEY",
        "BINANCE_API_SECRET",
        "BINANCE_LIVE_API_KEY",
        "BINANCE_LIVE_API_SECRET",
        "BINANCE_DEMO_API_KEY",
        "BINANCE_DEMO_API_SECRET",
    ]:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValueError, match="Missing"):
        DemoCredentials.from_environment()
    monkeypatch.setenv("BINANCE_DEMO_API_KEY", "synthetic-key")
    monkeypatch.setenv("BINANCE_DEMO_API_SECRET", "synthetic-secret")
    credentials = DemoCredentials.from_environment()
    assert "synthetic" not in repr(credentials)
    assert (
        REST_HOST == "https://demo-fapi.binance.com" and WS_HOST == "wss://demo-fstream.binance.com"
    )
    monkeypatch.setenv("BINANCE_API_KEY", "generic-key")
    with pytest.raises(ValueError, match="Mixed"):
        DemoCredentials.from_environment()
    with pytest.raises(ValueError):
        DemoCredentials(SecretStr("x"), SecretStr("y"), environment="LIVE")


def test_config_rejects_live_toggle_and_live_mode():
    for update in [{"mode": "LIVE"}, {"live_trading_enabled": True}]:
        with pytest.raises(ValueError):
            DemoSettings.model_validate(settings().model_dump() | update)


def test_default_blocks_every_mutation_and_private_session():
    c, fake = client(False)
    with pytest.raises(DemoFailure):
        c.request("POST", "/fapi/v1/order", {"type": "LIMIT"})
    with pytest.raises(DemoFailure):
        c.listen_key("POST")
    assert all(method == "GET" for method, path, params in fake.calls)


@pytest.mark.parametrize(
    "path",
    [
        "https://fapi.binance.com/fapi/v1/order",
        "//fapi.binance.com/fapi/v1/order",
        "/fapi/v1/order/test",
        "/fapi/v1/leverage",
        "/fapi/v1/positionSide/dual",
    ],
)
def test_unsupported_write_paths_are_rejected_before_transport(path):
    c, fake = client()
    count = len(fake.calls)
    with pytest.raises(ValueError):
        c.request("POST", path, {})
    assert len(fake.calls) == count


@pytest.mark.parametrize(
    "path,params",
    [
        ("/fapi/v1/order", {"type": "MARKET"}),
        ("/fapi/v1/order", {"type": "STOP_MARKET"}),
        (
            "/fapi/v1/algoOrder",
            {
                "type": "STOP_MARKET",
                "algoType": "CONDITIONAL",
                "closePosition": "true",
                "quantity": "1",
            },
        ),
        (
            "/fapi/v1/algoOrder",
            {
                "type": "STOP_MARKET",
                "algoType": "CONDITIONAL",
                "closePosition": "true",
                "reduceOnly": "true",
            },
        ),
    ],
)
def test_new_entries_cannot_use_market_or_legacy_conditional_orders(path, params):
    c, fake = client()
    count = len(fake.calls)
    with pytest.raises(ValueError):
        c.request("POST", path, params)
    assert len(fake.calls) == count


def test_signing_sends_hmac_but_never_includes_secret():
    c, fake = client()
    captured = []

    def send(method, path, query, key):
        captured.append(query.get_secret_value())
        return RESTResponse(200, b"{}", {})

    c.send = send
    c.request("GET", "/fapi/v3/account")
    query = captured[0]
    body, signature = query.rsplit("&signature=", 1)
    assert signature == sign_payload(SecretStr("synthetic-secret"), body)
    assert "synthetic-secret" not in query
    assert parse_qs(body)["recvWindow"] == ["5000"]


@pytest.mark.parametrize(
    "status,body,error",
    [
        (503, b'{"code":-1007,"msg":"synthetic-secret"}', DemoUnknown),
        (200, b"not-json synthetic-secret", DemoUnknown),
        (400, b'{"code":-2019,"msg":"synthetic-secret"}', DemoRejected),
        (401, b'{"code":-2015,"msg":"synthetic-secret"}', DemoUnknown),
        (302, b'{"msg":"synthetic-secret"}', DemoUnknown),
    ],
)
def test_uncertain_errors_are_sanitized_and_not_retried(status, body, error):
    c, fake = client()
    calls = []

    def send(*args):
        calls.append(args)
        return RESTResponse(status, body, {})

    c.send = send
    with pytest.raises(error) as caught:
        c.request("POST", "/fapi/v1/order", {"type": "LIMIT"})
    assert len(calls) == 1 and "synthetic-secret" not in str(caught.value)


def test_not_found_is_not_an_order_rejection():
    c, fake = client()
    with pytest.raises(DemoNotFound):
        c.request("GET", "/fapi/v1/order", {"symbol": "BTCUSDT", "origClientOrderId": "unknown"})


def test_clock_skew_staleness_and_rate_limit_block_subsequent_requests():
    c, fake = client()
    fake.at += timedelta(seconds=61)
    with pytest.raises(DemoFailure):
        c.request("GET", "/fapi/v3/account")
    c.sync_clock()
    fake.rate_limit = True
    with pytest.raises(DemoRateLimited):
        c.request("GET", "/fapi/v3/account")
    calls = len(fake.calls)
    with pytest.raises(DemoFailure):
        c.request("POST", "/fapi/v1/order", {"type": "LIMIT"})
    assert len(fake.calls) == calls
    c, fake = client()
    c.send = lambda *args: RESTResponse(200, b'{"serverTime":1}', {})
    with pytest.raises(DemoFailure):
        c.sync_clock()
    assert c.synced_at is None


def test_private_url_never_uses_production_host_or_exposes_key_repr():
    # Valid path characters are explicitly bounded; no query/fragment/host injection.
    from integrations.binance_usdm.demo import DemoClient

    with pytest.raises(ValueError):
        DemoClient.private_url(SecretStr("https://fapi.binance.com/key"))
    url = DemoClient.private_url(SecretStr("syntheticPrivateKey123456"))
    assert url.get_secret_value().startswith(WS_HOST + "/private/ws/") and "synthetic" not in repr(
        url
    )


def test_transport_exception_cannot_leak_signed_url_or_api_key():
    c, fake = client()

    def fail(*args):
        raise RuntimeError("synthetic-secret synthetic-key signed-url")

    c.send = fail
    with pytest.raises(DemoUnknown) as caught:
        c.submit(OrderIntent("/fapi/v1/order", (("type", "LIMIT"),)))
    assert "synthetic" not in str(caught.value)


def test_network_transport_pins_demo_host_uses_form_body_and_forbids_redirects(monkeypatch):
    from integrations.binance_usdm import demo
    from integrations.binance_usdm.rest import NoRedirect

    captures = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, size):
            assert size == 4_194_305
            return b"{}"

    class Opener:
        def open(self, request, timeout):
            captures.append(request)
            assert timeout == 5
            return Response()

    def opener(handler):
        assert isinstance(handler, NoRedirect)
        return Opener()

    monkeypatch.setattr(demo, "build_opener", opener)
    demo.transport(
        "POST",
        "/fapi/v1/order",
        SecretStr("type=LIMIT&signature=synthetic"),
        SecretStr("synthetic-key"),
    )
    request = captures[0]
    assert request.full_url == REST_HOST + "/fapi/v1/order"
    assert request.data == b"type=LIMIT&signature=synthetic"
    assert request.get_header("X-mbx-apikey") == "synthetic-key"
    with pytest.raises(ValueError):
        demo.transport("POST", "https://fapi.binance.com/fapi/v1/order", SecretStr(""), None)
    assert len(captures) == 1


@pytest.mark.parametrize(
    "flags,live,expected",
    [
        ([], False, "Missing direct runtime BINANCE_DEMO_API_KEY/BINANCE_DEMO_API_SECRET"),
        ([], True, "LIVE context is forbidden"),
        (["--watch"], False, "DEMO writes require explicit"),
        (["--watch", "--enable-demo", "--resume-entries"], False, "stable --operation-id"),
        (
            ["--enable-demo", "--signal", "not-read.json"],
            False,
            "--signal requires continuous --watch",
        ),
    ],
)
def test_actual_cli_refuses_missing_credentials_or_unsafe_activation_without_traceback(
    flags, live, expected
):
    import os
    import subprocess
    import sys

    env = os.environ.copy()
    for name in [
        "BINANCE_DEMO_API_KEY",
        "BINANCE_DEMO_API_SECRET",
        "BINANCE_API_KEY",
        "BINANCE_API_SECRET",
        "BINANCE_LIVE_API_KEY",
        "BINANCE_LIVE_API_SECRET",
    ]:
        env.pop(name, None)
    env["LIVE_TRADING_ENABLED"] = "true" if live else "false"
    env["TRADING_MODE"] = "PAPER"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.run_demo",
            "--config",
            "configs/demo.example.yaml",
            "--account",
            "DEMO-refusal-test",
            *flags,
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2 and expected in result.stderr and "Traceback" not in result.stderr
