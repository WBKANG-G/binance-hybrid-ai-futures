"""Fixed duration, explicit opt-in, LIVE rejection and all order mutations denied."""

import json
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from pydantic import SecretStr

from integrations.binance_usdm.demo import DemoCredentials, DemoFailure, DemoSettings
from scripts import check_demo_private_stability as cli
from services.execution.demo_private_stability import OBSERVATION_SECONDS, StabilityClient


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--observe-30-minutes"],
        ["--operation-id", str(uuid4())],
        ["--observe-30-minutes", "--operation-id", "bad"],
        ["--observe-30-minutes", "--operation-id", str(uuid4()), "--seconds", "1"],
    ],
)
def test_opt_in_uuid_and_fixed_duration_before_key_access(monkeypatch, args):
    monkeypatch.setattr("sys.argv", ["check"] + args)
    monkeypatch.setattr(cli, "credentials", lambda **kw: pytest.fail("must not read key"))
    with pytest.raises(SystemExit) as caught:
        cli.main()
    assert caught.value.code == 2


@pytest.mark.parametrize("env", [{"LIVE_TRADING_ENABLED": "true"}, {"TRADING_MODE": "LIVE"}])
def test_live_is_rejected_before_key_access(monkeypatch, capsys, env):
    monkeypatch.setattr(
        "sys.argv", ["check", "--observe-30-minutes", "--operation-id", str(uuid4())]
    )
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(cli, "credentials", lambda **kw: pytest.fail("must not read key"))
    with pytest.raises(SystemExit):
        cli.main()
    assert json.loads(capsys.readouterr().out)["failure"] == "LIVE_CONTEXT_FORBIDDEN"


def test_production_observation_period_is_thirty_minutes():
    assert OBSERVATION_SECONDS == 1800


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/fapi/v1/order"),
        ("DELETE", "/fapi/v1/order"),
        ("POST", "/fapi/v1/algoOrder"),
        ("POST", "/fapi/v1/leverage"),
    ],
)
def test_stability_client_blocks_every_non_get_before_transport(method, path):
    cfg = DemoSettings.model_validate(
        yaml.safe_load(Path("configs/demo-isolated.example.yaml").read_text())
    ).model_copy(update={"demo_trading_enabled": True})
    client = StabilityClient(
        cfg,
        DemoCredentials(SecretStr("fixture-key"), SecretStr("fixture-secret")),
        send=lambda *args: pytest.fail("must not send"),
    )
    with pytest.raises(DemoFailure):
        client.request(method, path, {})
    assert (
        client.read_queries == client.key_creates == client.key_renewals == client.key_closes == 0
    )
