"""Explicit intent, context restrictions and sanitized failure output."""

import json

import pytest

import scripts.check_demo_disconnect as cli
from integrations.binance_usdm.demo import DemoFailure
from scripts.observe_demo_private import ObservationClient


def test_flag_is_required_before_reading_credentials(monkeypatch):
    monkeypatch.setattr("sys.argv", ["check_demo_disconnect"])
    monkeypatch.setattr(cli, "credentials", lambda **kw: pytest.fail("must not read keys"))
    with pytest.raises(SystemExit) as caught:
        cli.main()
    assert caught.value.code == 2


@pytest.mark.parametrize("context", ["LIVE_TRADING_ENABLED", "TRADING_MODE"])
def test_live_context_rejected_before_credentials(monkeypatch, capsys, context):
    monkeypatch.setattr("sys.argv", ["check_demo_disconnect", "--enable-fault-injection"])
    monkeypatch.setenv(context, "true" if context == "LIVE_TRADING_ENABLED" else "LIVE")
    monkeypatch.setattr(cli, "credentials", lambda **kw: pytest.fail("must not read keys"))
    with pytest.raises(SystemExit):
        cli.main()
    assert json.loads(capsys.readouterr().out)["code"] == "LIVE_CONTEXT_FORBIDDEN"


def test_raw_private_failure_is_never_printed(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["check_demo_disconnect", "--enable-fault-injection"])

    def fail(**kw):
        raise RuntimeError("PRIVATE_API_KEY_URL_RAW_ERROR")

    monkeypatch.setattr(cli, "credentials", fail)
    with pytest.raises(SystemExit):
        cli.main()
    output = capsys.readouterr().out
    assert "PRIVATE_API_KEY" not in output
    assert json.loads(output)["code"] == "CHECK_FAILED"


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH"])
def test_existing_order_mutation_guard_applies_before_transport(method):
    with pytest.raises(DemoFailure, match="forbids"):
        object.__new__(ObservationClient).request(method, "/fapi/v1/order")


def test_non_observation_client_rejected_before_store_access():
    from types import SimpleNamespace

    with pytest.raises(cli.CheckBlocked, match="READ_ONLY_CLIENT_REQUIRED"):
        cli.empty_state(SimpleNamespace(client=object()))
