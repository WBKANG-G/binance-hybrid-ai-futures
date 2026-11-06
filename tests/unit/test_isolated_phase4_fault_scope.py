"""The isolated runner must reject a trading context before opening a database."""

import json

import pytest

from scripts import run_isolated_phase4_fault_check as runner


@pytest.mark.parametrize(
    "fault", ["flag", "database", "live", "mode", "main_db_env", "binance_env", "key_mount"]
)
def test_scope_rejection_precedes_any_database_or_test_execution(fault, monkeypatch, capsys):
    monkeypatch.setenv("ISOLATED_PHASE4_FAULT_CHECK", "1")
    monkeypatch.setenv("TEST_DATABASE_URL", runner.DATABASE)
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    for name in list(runner.os.environ):
        if name.startswith("BINANCE_"):
            monkeypatch.delenv(name)
    monkeypatch.setattr(runner.Path, "exists", lambda self: fault == "key_mount")
    if fault == "flag":
        monkeypatch.delenv("ISOLATED_PHASE4_FAULT_CHECK")
    if fault == "database":
        monkeypatch.setenv(
            "TEST_DATABASE_URL", "postgresql+psycopg://dev:dev@127.0.0.1:5432/hybrid"
        )
    if fault == "live":
        monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    if fault == "mode":
        monkeypatch.setenv("TRADING_MODE", "DEMO")
    if fault == "main_db_env":
        monkeypatch.setenv("DATABASE_URL", "PRIVATE_DATABASE_URL")
    if fault == "binance_env":
        monkeypatch.setenv("BINANCE_DEMO_API_KEY", "PRIVATE_KEY")
    monkeypatch.setattr(
        runner, "create_engine", lambda *a, **kw: pytest.fail("must reject before DB")
    )
    monkeypatch.setattr(
        runner.pytest, "main", lambda *a, **kw: pytest.fail("must reject before tests")
    )
    with pytest.raises(SystemExit) as caught:
        runner.main()
    assert caught.value.code == 2
    output = capsys.readouterr().out
    assert "PRIVATE_KEY" not in output and "PRIVATE_DATABASE_URL" not in output
    assert json.loads(output)["fault_check"] == "BLOCKED_SCOPE_OR_TEST_DATABASE"
