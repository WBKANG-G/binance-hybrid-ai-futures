import json
from types import SimpleNamespace

import pytest

from scripts import review_demo_quota_startup as cli


def args(monkeypatch, *extra):
    monkeypatch.setattr("sys.argv", ["review", "--journal-directory", "/not-created", *extra])
    monkeypatch.delenv("LIVE_TRADING_ENABLED", raising=False)
    monkeypatch.delenv("TRADING_MODE", raising=False)


def forbidden(*args, **kwargs):
    raise AssertionError("Database must not be opened")


@pytest.mark.parametrize("name,value", [("LIVE_TRADING_ENABLED", "true"), ("TRADING_MODE", "live")])
def test_live_context_stops_before_database(monkeypatch, capsys, name, value):
    args(monkeypatch)
    monkeypatch.setenv(name, value)
    monkeypatch.setattr(cli, "create_engine", forbidden)
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 2
    assert json.loads(capsys.readouterr().out)["blockers"] == ["LIVE_CONTEXT_FORBIDDEN"]


def test_invalid_account_stops_before_database(monkeypatch, capsys):
    args(monkeypatch, "--account", "LIVE-private")
    monkeypatch.setattr(cli, "create_engine", forbidden)
    with pytest.raises(SystemExit):
        cli.main()
    assert json.loads(capsys.readouterr().out)["blockers"] == ["DEMO_ACCOUNT_REQUIRED"]


def test_cli_has_no_apply_or_bootstrap_option(monkeypatch, capsys):
    args(monkeypatch, "--apply")
    monkeypatch.setattr(cli, "create_engine", forbidden)
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 2
    assert not capsys.readouterr().out


def test_database_error_does_not_print_url_or_raw_exception(monkeypatch, capsys):
    args(monkeypatch)
    monkeypatch.setattr(
        cli, "database_url", lambda: SimpleNamespace(get_secret_value=lambda: "private-url")
    )

    def failed(*args, **kwargs):
        raise ValueError("private-url secret-key /private/path")

    monkeypatch.setattr(cli, "create_engine", failed)
    with pytest.raises(SystemExit):
        cli.main()
    out = capsys.readouterr().out
    assert "private" not in out and "secret-key" not in out
    assert json.loads(out)["blockers"] == ["LOCAL_REVIEW_FAILED"]


def test_success_still_grants_no_execution_and_disposes_engine(monkeypatch, capsys):
    args(monkeypatch)
    disposed = []
    engine = SimpleNamespace(dispose=lambda: disposed.append(True))
    monkeypatch.setattr(
        cli, "database_url", lambda: SimpleNamespace(get_secret_value=lambda: "synthetic")
    )
    monkeypatch.setattr(cli, "create_engine", lambda *a, **k: engine)
    report = cli.stopped("unused")
    report.update(local_checks_passed=True, blockers=[])
    monkeypatch.setattr(cli, "review_startup", lambda *a, **k: report)
    cli.main()
    out = json.loads(capsys.readouterr().out)
    assert disposed == [True] and not out["execution_approved"] and out["http_attempts"] == 0
