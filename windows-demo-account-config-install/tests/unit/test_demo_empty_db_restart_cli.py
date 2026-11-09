"""Checkpoint flags and LIVE guard precede credentials; no Docker API in the tool."""

import json
from uuid import uuid4

import pytest

from scripts import check_demo_empty_db_restart as cli
from services.execution.demo_empty_db_restart import DBRestartBlocked


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--prepare"],
        ["--verify"],
        ["--prepare", "--verify", "--operation-id", str(uuid4())],
        ["--prepare", "--operation-id", "bad"],
    ],
)
def test_flags_before_credentials(monkeypatch, args):
    monkeypatch.setattr("sys.argv", ["check"] + args)
    monkeypatch.setattr(cli, "credentials", lambda **kw: pytest.fail("must not read key"))
    with pytest.raises(SystemExit) as caught:
        cli.main()
    assert caught.value.code == 2


@pytest.mark.parametrize("env", [{"LIVE_TRADING_ENABLED": "true"}, {"TRADING_MODE": "LIVE"}])
def test_live_context_blocked_before_credentials(monkeypatch, capsys, env):
    monkeypatch.setattr("sys.argv", ["check", "--prepare", "--operation-id", str(uuid4())])
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(cli, "credentials", lambda **kw: pytest.fail("must not read key"))
    with pytest.raises(SystemExit):
        cli.main()
    assert json.loads(capsys.readouterr().out)["failure"] == "LIVE_CONTEXT_FORBIDDEN"


def test_unknown_failure_code_cannot_export_private_values():
    assert DBRestartBlocked("PRIVATE_DATABASE_URL").code == "CHECK_FAILED"
