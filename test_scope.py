"""Runner guard rejects unsafe scope before DB/imported application execution."""

import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "expiry_runner", Path(__file__).with_name("run_isolated_expiry_check.py")
)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


@pytest.mark.parametrize(
    "change",
    [
        {"ISOLATED_PHASE4_FAULT_CHECK": "0"},
        {"TEST_DATABASE_URL": "other"},
        {"DATABASE_URL": "synthetic"},
        {"BINANCE_DEMO_API_KEY": "synthetic"},
        {"LIVE_TRADING_ENABLED": "true"},
        {"TRADING_MODE": "DEMO"},
        {"TRADING_MODE": "LIVE"},
    ],
)
def test_foreign_or_keyed_context_rejected_before_io(monkeypatch, change):
    monkeypatch.setattr(
        runner.os,
        "environ",
        {
            "ISOLATED_PHASE4_FAULT_CHECK": "1",
            "TEST_DATABASE_URL": runner.DATABASE,
            **change,
        },
    )
    monkeypatch.setattr(runner.Path, "cwd", lambda: Path("/app"))
    monkeypatch.setattr(runner.Path, "exists", lambda self: False)
    with pytest.raises(ValueError, match="ISOLATED_SCOPE_REQUIRED"):
        runner.scope()


def test_valid_keyless_context_and_cwd_or_mount_rejection(monkeypatch):
    monkeypatch.setattr(
        runner.os,
        "environ",
        {"ISOLATED_PHASE4_FAULT_CHECK": "1", "TEST_DATABASE_URL": runner.DATABASE},
    )
    monkeypatch.setattr(runner.Path, "cwd", lambda: Path("/app"))
    monkeypatch.setattr(runner.Path, "exists", lambda self: False)
    runner.scope()
    monkeypatch.setattr(runner.Path, "cwd", lambda: Path("/other"))
    with pytest.raises(ValueError):
        runner.scope()
    monkeypatch.setattr(runner.Path, "cwd", lambda: Path("/app"))
    monkeypatch.setattr(runner.Path, "exists", lambda self: True)
    with pytest.raises(ValueError):
        runner.scope()
