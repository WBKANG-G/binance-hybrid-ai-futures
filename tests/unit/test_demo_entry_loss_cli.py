"""Explicit DEMO/local fault consent and secondary transport safety barrier."""

import json
from uuid import uuid4

import pytest

from scripts import check_demo_entry_response_loss as cli


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--enable-demo"],
        ["--enable-entry-response-loss"],
        ["--enable-demo", "--enable-entry-response-loss"],
        ["--enable-demo", "--enable-entry-response-loss", "--operation-id", "bad"],
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
        ["check", "--enable-demo", "--enable-entry-response-loss", "--operation-id", str(uuid4())],
    )
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(cli, "credentials", lambda **kw: pytest.fail("must not read key"))
    with pytest.raises(SystemExit):
        cli.main()
    assert json.loads(capsys.readouterr().out)["failure"] == "LIVE_CONTEXT_FORBIDDEN"
