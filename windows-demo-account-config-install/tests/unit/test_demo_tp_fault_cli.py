"""Explicit DEMO/local fault consent and secondary transport safety barrier."""

import json
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from pydantic import SecretStr

from integrations.binance_usdm.demo import DemoCredentials, DemoSettings
from scripts import check_demo_tp_fault as cli
from services.execution.demo_tp_fault_check import TPFaultBlocked, TPFaultClient


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--enable-demo"],
        ["--enable-tp-build-fault"],
        ["--enable-demo", "--enable-tp-build-fault"],
        ["--enable-demo", "--enable-tp-build-fault", "--operation-id", "bad"],
        ["--enable-demo", "--operation-id", str(uuid4())],
    ],
)
def test_flags_precede_key_access(monkeypatch, args):
    monkeypatch.setattr("sys.argv", ["check"] + args)
    monkeypatch.setattr(cli, "credentials", lambda **kw: pytest.fail("must not read key"))
    with pytest.raises(SystemExit) as caught:
        cli.main()
    assert caught.value.code == 2


@pytest.mark.parametrize("env", [{"LIVE_TRADING_ENABLED": "true"}, {"TRADING_MODE": "LIVE"}])
def test_live_context_blocked_before_credentials(monkeypatch, capsys, env):
    monkeypatch.setattr(
        "sys.argv",
        ["check", "--enable-demo", "--enable-tp-build-fault", "--operation-id", str(uuid4())],
    )
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(cli, "credentials", lambda **kw: pytest.fail("must not read key"))
    with pytest.raises(SystemExit):
        cli.main()
    assert json.loads(capsys.readouterr().out)["failure"] == "LIVE_CONTEXT_FORBIDDEN"


def test_own_tp_cannot_reach_transport_even_if_builder_fault_is_bypassed():
    cfg = DemoSettings.model_validate(
        yaml.safe_load(Path("configs/demo-isolated.example.yaml").read_text())
    ).model_copy(update={"demo_trading_enabled": True})
    client = TPFaultClient(
        cfg,
        DemoCredentials(SecretStr("fixture-key"), SecretStr("fixture-secret")),
        send=lambda *a: pytest.fail("must not send"),
    )
    client.block_tp_id = "DT-fixture"
    with pytest.raises(TPFaultBlocked) as caught:
        client.request(
            "POST",
            "/fapi/v1/algoOrder",
            {"clientAlgoId": "DT-fixture", "type": "TAKE_PROFIT_MARKET"},
        )
    assert caught.value.code == "UNEXPECTED_TP_TRANSPORT"
    assert client.unexpected_tp_attempts == 1 and client.entry_post_sent == 0
