import copy
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("fix_guard", Path("guard_tp_read_pool_install.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
SAFE = {
    "recovery": "REVIEW_ONLY_NO_DATABASE_CHANGE",
    "risk_gates": "PASS",
    "position_nonzero": False,
    "active_orders": 0,
    "paused": True,
    "risk_state": "NORMAL",
    "orders_enabled": False,
    "live_trading_enabled": False,
    "unresolved_count": 4,
    "review_token": "SYNTHETIC_PRIVATE_TOKEN",
}


@pytest.mark.parametrize(
    "phase,digest", [("pre", m.OLD_SHA), ("pre", m.NEW_SHA), ("post", m.NEW_SHA)]
)
def test_only_read_review_is_called_and_token_never_returned(phase, digest):
    calls = []

    def local(engine):
        calls.append("LOCAL")
        return ("STABLE",)

    def review():
        calls.append("REVIEW_ONLY")
        return copy.deepcopy(SAFE)

    report = m.run(None, phase, read_local=local, read_review=review, digest=digest)
    assert calls == ["LOCAL", "REVIEW_ONLY", "LOCAL"]
    assert report["paused"] and not report["recovery_applied"] and not report["database_changed"]
    assert "SYNTHETIC_PRIVATE_TOKEN" not in str(report)


@pytest.mark.parametrize(
    "change",
    [
        {"risk_gates": "FAIL"},
        {"position_nonzero": True},
        {"active_orders": 1},
        {"paused": False},
        {"orders_enabled": True},
        {"live_trading_enabled": True},
        {"recovery": "APPLIED"},
        {"unresolved_count": True},
    ],
)
def test_unsafe_review_blocks_without_apply(change):
    with pytest.raises(ValueError):
        m.run(
            None,
            "pre",
            read_local=lambda e: ("STABLE",),
            read_review=lambda: SAFE | change,
            digest=m.OLD_SHA,
        )


def test_changed_account_blocks_after_review():
    rows = iter([("BEFORE",), ("CHANGED",)])
    with pytest.raises(ValueError):
        m.run(
            None, "pre", read_local=lambda e: next(rows), read_review=lambda: SAFE, digest=m.OLD_SHA
        )


def test_post_update_requires_new_runtime_source():
    with pytest.raises(ValueError):
        m.run(
            None, "post", digest=m.OLD_SHA, read_local=lambda e: pytest.fail("no DB for bad source")
        )


def test_cli_raw_failure_is_redacted(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["guard", "--phase", "pre"])

    def fail(*a, **k):
        raise ValueError("PRIVATE_CONNECTION_OR_TOKEN")

    monkeypatch.setattr(m, "create_engine", fail)
    with pytest.raises(SystemExit) as error:
        m.main()
    assert error.value.code == 2 and "PRIVATE_CONNECTION_OR_TOKEN" not in capsys.readouterr().out


def test_subprocess_command_has_no_apply_or_export_flags(monkeypatch):
    import json
    from types import SimpleNamespace

    captured = []

    def execute(args, **kwargs):
        captured.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps(SAFE))

    monkeypatch.setattr(m.subprocess, "run", execute)
    assert m.review()["risk_gates"] == "PASS"
    args, options = captured[0]
    assert args == [m.sys.executable, "-m", "scripts.review_demo_recovery"]
    assert options["stdin"] == m.subprocess.DEVNULL and options["timeout"] == 150
    assert "--apply-review" not in args and "env" not in options
