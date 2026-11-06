"""Safe diagnostics preserve all recovery gates and never print raw errors or keys."""

import json
from datetime import timedelta

import pytest

from integrations.binance_usdm.demo import DemoUnknown
from services.execution.demo_recovery import RecoveryBlocked, review
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_recovery import recovery  # noqa: F401


def test_next_kst_day_reports_missing_baseline_without_changing_database(recovery):  # noqa: F811
    store, c, fake = recovery
    before = store.read("DEMO-test").model_dump_json()
    fake.at += timedelta(days=1)
    c.client.sync_clock()
    with pytest.raises(RecoveryBlocked) as result:
        review(store, c.client, store.read("DEMO-test"))
    assert result.value.code == "PERIOD_BASELINE_MISSING"
    assert result.value.periods["stored_day_kst"] != result.value.periods["current_day_kst"]
    assert store.read("DEMO-test").model_dump_json() == before


def test_same_day_loss_is_distinct_from_missing_baseline(recovery):  # noqa: F811
    store, c, fake = recovery
    before = store.read("DEMO-test").model_dump_json()
    fake.wallet = 1
    with pytest.raises(RecoveryBlocked) as result:
        review(store, c.client, store.read("DEMO-test"))
    assert result.value.code == "LOSS_OR_ACCOUNT_RISK_BLOCKED"
    assert not result.value.periods
    assert store.read("DEMO-test").model_dump_json() == before


def setup_cli(recovery, monkeypatch):  # noqa: F811
    import sys

    import scripts.review_demo_recovery as cli

    store, c, _ = recovery
    monkeypatch.setattr(sys, "argv", ["review_demo_recovery"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "SENSITIVE_SYNTHETIC_KEY")
    monkeypatch.setattr(cli, "create_engine", lambda *a, **k: store.engine)
    monkeypatch.setattr(cli, "DemoStore", lambda engine: store)
    monkeypatch.setattr(cli, "DemoClient", lambda settings, credentials: c.client)
    original_read = store.read
    monkeypatch.setattr(store, "read", lambda account: original_read("DEMO-test"))

    def cannot_acquire(*args):
        raise AssertionError("Read-only diagnostics must not acquire lease")

    monkeypatch.setattr(store, "acquire", cannot_acquire)
    return cli


@pytest.mark.parametrize("mode", ["period", "query", "clock", "configuration", "database"])
def test_cli_prints_safe_stage_and_code_then_cleans_credentials(
    recovery,  # noqa: F811
    monkeypatch,
    capsys,
    mode,
):  # noqa: F811
    import os

    from sqlalchemy.exc import SQLAlchemyError

    cli = setup_cli(recovery, monkeypatch)
    store, c, fake = recovery
    before = store.read("DEMO-test").model_dump_json()
    if mode == "period":
        fake.at += timedelta(days=1)
    elif mode in {"query", "clock"}:

        def private_error(*args):
            raise DemoUnknown("SENSITIVE_SYNTHETIC_KEY signed_url_secret")

        monkeypatch.setattr(
            cli if mode == "query" else c.client,
            "review" if mode == "query" else "sync_clock",
            private_error,
        )
    elif mode == "configuration":

        def wrong(*args, **kwargs):
            raise ValueError("SENSITIVE_SYNTHETIC_KEY")

        monkeypatch.setattr(cli.DemoSettings, "model_validate", wrong)
    else:

        def broken_db(*args, **kwargs):
            raise SQLAlchemyError("SENSITIVE_SYNTHETIC_KEY")

        monkeypatch.setattr(cli, "create_engine", broken_db)
    with pytest.raises(SystemExit) as exit_info:
        cli.main()
    assert exit_info.value.code == 2
    text = capsys.readouterr().out
    assert "SENSITIVE_SYNTHETIC_KEY" not in text and "signed_url_secret" not in text
    data = json.loads(text)
    expected = {
        "period": "PERIOD_BASELINE_MISSING",
        "query": "DEMO_QUERY_OR_SNAPSHOT_FAILED",
        "clock": "CLOCK_CHECK_FAILED",
        "configuration": "INPUT_OR_CONFIGURATION_INVALID",
        "database": "DATABASE_ACCESS_FAILED",
    }
    assert data["error_code"] == expected[mode]
    assert data["recovery"] == "BLOCKED_KEEP_LOCK"
    assert data["orders_enabled"] is False and data["live_trading_enabled"] is False
    assert "BINANCE_DEMO_API_KEY" not in os.environ and "BINANCE_DEMO_API_SECRET" not in os.environ
    assert store.read("DEMO-test").model_dump_json() == before


def test_period_diagnostics_only_export_dates_and_allowlisted_keys():
    error = RecoveryBlocked(
        "PERIOD_BASELINE_MISSING",
        "private raw message",
        periods={
            "stored_day_kst": "secret-value",
            "current_day_kst": "2026-10-21",
            "api_key": "secret-value",
        },
    )
    assert error.periods == {"stored_day_kst": "INVALID_DATE", "current_day_kst": "2026-10-21"}
    assert RecoveryBlocked("untrusted-secret", "private").code == "RECOVERY_CHECK_BLOCKED"


@pytest.mark.parametrize("next_day", [False, True])
def test_local_period_cli_needs_no_keys_or_exchange_and_preserves_lock(
    recovery,  # noqa: F811
    monkeypatch,
    capsys,
    next_day,
):
    import sys

    cli = setup_cli(recovery, monkeypatch)
    store, c, fake = recovery
    if next_day:
        fake.at += timedelta(days=1)
    before = store.read("DEMO-test").model_dump_json()
    start = len(fake.calls)

    def forbidden(*args, **kwargs):
        raise AssertionError("Local mode must not use keys or network")

    monkeypatch.setattr(cli.getpass, "getpass", forbidden)
    monkeypatch.setattr(c.client, "send", forbidden)
    monkeypatch.setattr(sys, "argv", ["review_demo_recovery", "--local-periods"])
    cli.main()
    data = json.loads(capsys.readouterr().out)
    assert data["period_keys_match"] is (not next_day)
    assert data["recovery"] == "LOCAL_PERIOD_CHECK_NO_DATABASE_CHANGE"
    assert data["risk_state"] == "EMERGENCY" and data["paused"] is True
    assert data["exchange_checked"] is False
    assert store.read("DEMO-test").model_dump_json() == before
    assert len(fake.calls) == start


def test_local_period_mode_cannot_apply_recovery(monkeypatch):
    import sys

    import scripts.review_demo_recovery as cli

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "review_demo_recovery",
            "--local-periods",
            "--apply-review",
            "a" * 64,
            "--operation-id",
            "00000000-0000-0000-0000-000000000001",
            "--acknowledge-unresolved-history",
        ],
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("Must reject before keys or DB access")

    monkeypatch.setattr(cli.getpass, "getpass", forbidden)
    monkeypatch.setattr(cli, "create_engine", forbidden)
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == 2
