"""Execution intent and independent-container restriction precede key access."""

import json
from pathlib import Path
from uuid import uuid4

import pytest
import yaml

import scripts.check_demo_open_api_restart as cli


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["--run"],
        ["--run", "--enable-demo"],
        ["--preview", "--enable-demo"],
        ["--preview", "--operation-id", str(uuid4())],
    ],
)
def test_missing_or_conflicting_execution_flags_never_read_keys(monkeypatch, arguments):
    monkeypatch.setattr("sys.argv", ["check"] + arguments)
    monkeypatch.setattr(cli, "credentials", lambda **kw: pytest.fail("must not read key"))
    with pytest.raises(SystemExit) as caught:
        cli.main()
    assert caught.value.code == 2


def test_run_cannot_be_started_by_exec_inside_api(monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.argv", ["check", "--run", "--enable-demo", "--operation-id", str(uuid4())]
    )
    monkeypatch.setattr(cli.os, "getpid", lambda: 99)
    monkeypatch.setattr(cli, "credentials", lambda **kw: pytest.fail("must not read key"))
    with pytest.raises(SystemExit):
        cli.main()
    assert json.loads(capsys.readouterr().out)["failure"] == "WORKER_CONTAINER_REQUIRED"


def test_live_context_is_blocked_before_key_access(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["check", "--preview"])
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    monkeypatch.setattr(cli, "credentials", lambda **kw: pytest.fail("must not read key"))
    with pytest.raises(SystemExit):
        cli.main()
    assert json.loads(capsys.readouterr().out)["restart_check"] == "BLOCKED"


def test_guard_is_independent_and_has_no_host_ports_or_docker_socket():
    data = yaml.safe_load(Path("infra/docker-compose.restart-check.yml").read_text())
    service = data["services"]["demo_restart_guard"]
    assert service["image"] == "hybrid-windows-api" and service["restart"] == "no"
    assert service["healthcheck"] == {"disable": True}
    assert "ports" not in service and "volumes" not in service
    assert service["environment"]["LIVE_TRADING_ENABLED"] == "false"
    assert service["profiles"] == ["open-api-restart-check"]
