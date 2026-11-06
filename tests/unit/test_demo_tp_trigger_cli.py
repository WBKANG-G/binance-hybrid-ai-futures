"""The real TP experiment is opt-in and rejects LIVE before any key or DB access."""

import json
import sys
from uuid import uuid4

import pytest

from scripts import check_demo_tp_trigger as cli


@pytest.mark.parametrize(
    "missing", ["--enable-demo", "--enable-tp-trigger-observation", "--operation-id"]
)
def test_each_explicit_argument_is_required_before_credentials(monkeypatch, missing):
    arguments = {
        "--enable-demo": [],
        "--enable-tp-trigger-observation": [],
        "--operation-id": [str(uuid4())],
    }
    argv = ["check"]
    for flag, values in arguments.items():
        if flag != missing:
            argv.extend([flag, *values])
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(cli, "credentials", lambda **kw: pytest.fail("must not read keys"))
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2


@pytest.mark.parametrize("name,value", [("TRADING_MODE", "LIVE"), ("LIVE_TRADING_ENABLED", "true")])
def test_live_context_is_rejected_before_keys_or_engine(monkeypatch, capsys, name, value):
    monkeypatch.setenv(name, value)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "check",
            "--enable-demo",
            "--enable-tp-trigger-observation",
            "--operation-id",
            str(uuid4()),
        ],
    )
    monkeypatch.setattr(cli, "credentials", lambda **kw: pytest.fail("must not read keys"))
    monkeypatch.setattr(cli, "create_engine", lambda *a, **kw: pytest.fail("must not access DB"))
    with pytest.raises(SystemExit):
        cli.main()
    report = json.loads(capsys.readouterr().out)
    assert report["tp_trigger_check"] == "BLOCKED"
    assert not report["orders_enabled"] and not report["live_trading_enabled"]


def test_unexpected_secret_exception_never_escapes_in_output(monkeypatch, capsys):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "check",
            "--enable-demo",
            "--enable-tp-trigger-observation",
            "--operation-id",
            str(uuid4()),
        ],
    )

    def fail(**kwargs):
        raise RuntimeError("SYNTHETIC_SECRET_MUST_NOT_ESCAPE")

    monkeypatch.setattr(cli, "credentials", fail)
    with pytest.raises(SystemExit):
        cli.main()
    output = capsys.readouterr().out
    assert "SYNTHETIC_SECRET" not in output and '"BLOCKED"' in output
