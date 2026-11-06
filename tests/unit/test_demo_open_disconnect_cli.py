"""Fault injection and DEMO opt-ins must precede credential access."""

import json
from uuid import uuid4

import pytest

from scripts import check_demo_open_disconnect as cli


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["--enable-demo"],
        ["--enable-fault-injection"],
        ["--enable-demo", "--enable-fault-injection"],
        ["--enable-demo", "--operation-id", str(uuid4())],
        ["--enable-demo", "--enable-fault-injection", "--operation-id", "not-a-uuid"],
    ],
)
def test_execution_flags_are_required_before_credentials(monkeypatch, arguments):
    monkeypatch.setattr("sys.argv", ["check"] + arguments)
    monkeypatch.setattr(cli, "credentials", lambda **kw: pytest.fail("key must not be read"))
    with pytest.raises(SystemExit) as caught:
        cli.main()
    assert caught.value.code == 2


@pytest.mark.parametrize("env", [{"LIVE_TRADING_ENABLED": "true"}, {"TRADING_MODE": "LIVE"}])
def test_live_context_blocked_before_credentials(monkeypatch, capsys, env):
    monkeypatch.setattr(
        "sys.argv",
        ["check", "--enable-demo", "--enable-fault-injection", "--operation-id", str(uuid4())],
    )
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(cli, "credentials", lambda **kw: pytest.fail("key must not be read"))
    with pytest.raises(SystemExit) as caught:
        cli.main()
    assert caught.value.code == 2
    assert json.loads(capsys.readouterr().out)["failure"] == "LIVE_CONTEXT_FORBIDDEN"


def test_credential_errors_are_sanitized_and_never_open_database(monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.argv",
        ["check", "--enable-demo", "--enable-fault-injection", "--operation-id", str(uuid4())],
    )

    def broken(**kwargs):
        raise ValueError("PRIVATE_KEY_MUST_NOT_APPEAR")

    monkeypatch.setattr(cli, "credentials", broken)
    monkeypatch.setattr(cli, "create_engine", lambda *a, **kw: pytest.fail("must not touch DB"))
    with pytest.raises(SystemExit):
        cli.main()
    output = capsys.readouterr().out
    assert "PRIVATE" not in output
    assert json.loads(output)["orders_enabled"] is False
