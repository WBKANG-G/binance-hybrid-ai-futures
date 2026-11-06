"""CLI opt-in, secret-free child launch and read-only SDK restrictions."""

import json
import subprocess
from uuid import uuid4

import pytest

from scripts import check_demo_empty_worker_recovery as cli


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--enable-fault-injection"],
        ["--operation-id", str(uuid4())],
        ["--enable-fault-injection", "--operation-id", "bad"],
        ["--enable-fault-injection", "--operation-id", str(uuid4()), "--worker"],
        ["--enable-fault-injection", "--operation-id", str(uuid4()), "--owner-id", str(uuid4())],
    ],
)
def test_flags_are_checked_before_keys(monkeypatch, args):
    monkeypatch.setattr("sys.argv", ["check"] + args)
    monkeypatch.setattr(cli, "credentials", lambda **kw: pytest.fail("must not read key"))
    with pytest.raises(SystemExit) as caught:
        cli.main()
    assert caught.value.code == 2


@pytest.mark.parametrize("context", [{"LIVE_TRADING_ENABLED": "true"}, {"TRADING_MODE": "LIVE"}])
def test_live_blocked_before_credentials_or_process(monkeypatch, capsys, context):
    monkeypatch.setattr(
        "sys.argv", ["check", "--enable-fault-injection", "--operation-id", str(uuid4())]
    )
    for key, value in context.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(cli, "credentials", lambda **kw: pytest.fail("must not read key"))
    with pytest.raises(SystemExit):
        cli.main()
    assert json.loads(capsys.readouterr().out)["failure"] == "LIVE_CONTEXT_FORBIDDEN"


def test_child_has_no_secrets_in_argv_or_extra_environment(monkeypatch):
    calls = []

    def launch(*args, **kwargs):
        calls.append((args, kwargs))
        return object()

    monkeypatch.setattr(cli.subprocess, "Popen", launch)
    operation, owner = uuid4(), uuid4()
    cli.spawn_worker(operation, owner)
    args, kwargs = calls[0]
    assert args[0] == [
        cli.sys.executable,
        "-m",
        "scripts.check_demo_empty_worker_recovery",
        "--enable-fault-injection",
        "--operation-id",
        str(operation),
        "--worker",
        "--owner-id",
        str(owner),
    ]
    assert "env" not in kwargs and "shell" not in kwargs
    assert kwargs == {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
