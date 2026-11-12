"""Local entrypoint never provisions, retries, sends, or silently repairs journal state."""

# ruff: noqa: F811
from uuid import uuid4

import pytest

from scripts import review_demo_read_journal as cli
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_read_audit import audited, reader, records  # noqa: F401
from tests.integration.test_demo_read_recovery import clocks
from tests.integration.test_runtime_read_accounting import financial, signed
from tests.integration.test_shared_weight_checkpoint import anchored  # noqa: F401
from tests.unit.test_quota_startup_read_only import files


def argv(case, operation="review"):
    a, _, shared = case
    return [
        operation,
        "--directory",
        str(a[3]),
        "--scope",
        shared.scope,
        "--journal",
        str(shared.identity),
        "--installation",
        str(a[1].installation_id()),
    ]


def engine_for(monkeypatch, case):
    monkeypatch.setattr(cli, "create_engine", lambda *a, **kw: case[0][1].engine)


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["review"],
        ["recover-clock"],
        ["review", "--secret", "PRIVATE"],
    ],
)
def test_bad_arguments_do_not_open_database(monkeypatch, args):
    monkeypatch.setattr(cli, "create_engine", lambda *a, **k: pytest.fail("database opened"))
    code, report = cli.run(args)
    assert code == 1 and report["stage"] == "ARGUMENTS"
    assert report["stop_reason"] == "READ_OPERATION_ARGUMENTS_INVALID"
    assert "PRIVATE" not in str(report)


@pytest.mark.parametrize(
    "kind",
    [
        "relative",
        "scope",
        "apply",
        "stopped",
        "recover",
        "recover_apply",
        "recover_stopped",
        "uuid",
    ],
)
def test_invalid_operation_flags_rejected_before_database(audited, monkeypatch, kind):
    args = argv(audited)
    if kind == "relative":
        args[2] = "."
    elif kind == "scope":
        args[4] = "LIVE-test"
    elif kind == "uuid":
        args[6] = "invalid"
    elif kind in {"apply", "stopped"}:
        args += ["--apply" if kind == "apply" else "--readers-stopped"]
    else:
        args[0] = "recover-clock"
        args += (
            ["--apply"]
            if kind == "recover_apply"
            else ["--readers-stopped"]
            if kind == "recover_stopped"
            else []
        )
    monkeypatch.setattr(cli, "create_engine", lambda *a, **k: pytest.fail("database opened"))
    assert cli.run(args)[1]["stop_reason"] == "READ_OPERATION_ARGUMENTS_INVALID"


@pytest.mark.parametrize(
    "name,value",
    [
        ("LIVE_TRADING_ENABLED", "true"),
        ("TRADING_MODE", "LIVE"),
        ("DATABASE_URL", "sqlite:///PRIVATE"),
    ],
)
def test_live_or_non_postgres_configuration_does_not_connect(audited, monkeypatch, name, value):
    args = argv(audited)
    monkeypatch.setenv(name, value)
    monkeypatch.setattr(cli, "create_engine", lambda *a, **k: pytest.fail("database opened"))
    code, report = cli.run(args)
    assert code == 1 and report["stage"] == "ENVIRONMENT"
    assert "PRIVATE" not in str(report)


def test_review_preserves_files_events_checkpoints_and_financial_state(audited, monkeypatch):
    a, _, _ = audited
    reader(audited)(*signed())
    before, events, fin = files(a[3]), records(a), financial(a)
    engine_for(monkeypatch, audited)
    code, report = cli.run(argv(audited), clocks=clocks(audited, "valid"))
    assert code == 0 and report["successful_reads"] == 1
    assert report["read_journal_operation"] == "LOCAL_REVIEW_COMPLETE_KEEP_PAUSED"
    assert not report["account_state_checked"] and not report["execution_approved"]
    assert files(a[3]) == before and records(a) == events and financial(a) == fin


def test_installation_mismatch_does_not_touch_journal(audited, monkeypatch):
    args = argv(audited)
    args[-1] = str(uuid4())
    before = files(audited[0][3])
    engine_for(monkeypatch, audited)
    code, report = cli.run(args)
    assert code == 1 and report["stop_reason"] == "READ_OPERATION_INSTALLATION_MISMATCH"
    assert files(audited[0][3]) == before


def test_clock_review_refuses_without_automatic_recovery(audited, monkeypatch):
    before = files(audited[0][3])
    engine_for(monkeypatch, audited)
    code, report = cli.run(argv(audited), clocks=clocks(audited))
    assert code == 1 and report["stage"] == "JOURNAL_REVIEW"
    assert files(audited[0][3]) == before


def test_explicit_recovery_then_review_honors_startup_hold(audited, monkeypatch):
    a, _, _ = audited
    reader(audited)(*signed())
    fin = financial(a)
    engine_for(monkeypatch, audited)
    current = clocks(audited)
    args = argv(audited, "recover-clock") + ["--apply", "--readers-stopped"]
    code, report = cli.run(args, clocks=current)
    assert code == 2 and report["shared_journal_changed"]
    assert (
        report["readers_stopped_attested"] and not report["readers_stopped_independently_verified"]
    )
    assert report["shared_total_weight"] == 5
    before, events = files(a[3]), records(a)
    code, report = cli.run(argv(audited), clocks=current)
    assert code == 2 and report["read_journal_operation"] == "WAIT_STARTUP_HOLD"
    a[2].seconds += 70.002
    code, report = cli.run(argv(audited), clocks=current)
    assert code == 0 and report["startup_ready"] and report["shared_total_weight"] == 5
    assert files(a[3]) == before and records(a) == events and financial(a) == fin


def test_pending_review_reports_incomplete_and_recovery_refuses(audited, monkeypatch):
    a, _, shared = audited
    shared.reserve_read("/fapi/v3/account", 5)
    before, events = files(a[3]), records(a)
    engine_for(monkeypatch, audited)
    code, report = cli.run(argv(audited), clocks=clocks(audited, "valid"))
    assert code == 3 and report["pending_requests"] == 1
    args = argv(audited, "recover-clock") + ["--apply", "--readers-stopped"]
    code, report = cli.run(args, clocks=clocks(audited))
    assert code == 1 and report["stop_reason"] == "READ_RECOVERY_PENDING_REQUESTS"
    assert files(a[3]) == before and records(a) == events


def test_unknown_database_error_is_redacted_without_retry(audited, monkeypatch):
    args = argv(audited)
    calls = []

    def fail(*a, **kw):
        calls.append(1)
        raise RuntimeError("postgresql://PRIVATE_SECRET /private/path")

    monkeypatch.setattr(cli, "create_engine", fail)
    code, report = cli.run(args)
    assert code == 1 and len(calls) == 1
    assert report["stop_reason"] == "READ_OPERATION_UNAVAILABLE"
    assert "PRIVATE" not in str(report)


def test_missing_audit_schema_is_not_provisioned(anchored, monkeypatch):
    from sqlalchemy import inspect

    case = anchored, None, anchored[0]
    before = files(anchored[3])
    tables = inspect(anchored[1].engine).get_table_names()
    engine_for(monkeypatch, case)
    code, report = cli.run(argv(case), clocks=clocks(case, "valid"))
    assert code == 1 and report["stage"] == "JOURNAL_REVIEW"
    assert inspect(anchored[1].engine).get_table_names() == tables
    assert files(anchored[3]) == before


def test_main_emits_one_json_record_and_preserves_exit_status(monkeypatch, capsys):
    import json
    import sys

    monkeypatch.setattr(sys, "argv", ["review_demo_read_journal", "review"])
    assert cli.main() == 1
    output = capsys.readouterr()
    report = json.loads(output.out)
    assert len(output.out.splitlines()) == 1 and output.err == ""
    assert report["stop_reason"] == "READ_OPERATION_ARGUMENTS_INVALID"


def test_connection_configuration_has_timeouts_and_hides_parameters(audited, monkeypatch):
    captured = {}

    def factory(url, **kwargs):
        captured.update(kwargs)
        return audited[0][1].engine

    monkeypatch.setattr(cli, "create_engine", factory)
    assert cli.run(argv(audited), clocks=clocks(audited, "valid"))[0] == 0
    assert captured["hide_parameters"] is True
    assert captured["connect_args"]["connect_timeout"] == 5
    assert "statement_timeout=2000" in captured["connect_args"]["options"]
