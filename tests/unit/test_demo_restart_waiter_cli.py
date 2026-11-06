"""No restart eligibility is emitted on missing context, timeout or private errors."""

import json
from uuid import uuid4

import pytest

import scripts.wait_demo_restart_ready as waiter


def test_waiter_requires_exact_operation_id_before_database(monkeypatch):
    monkeypatch.setattr("sys.argv", ["wait"])
    monkeypatch.setattr(waiter, "create_engine", lambda *a, **kw: pytest.fail("no database access"))
    with pytest.raises(SystemExit):
        waiter.main()


def test_waiter_rejects_live_before_database(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["wait", "--operation-id", str(uuid4())])
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    monkeypatch.setattr(waiter, "create_engine", lambda *a, **kw: pytest.fail("no database access"))
    with pytest.raises(SystemExit):
        waiter.main()
    assert json.loads(capsys.readouterr().out)["restart_handoff"] == "LIVE_CONTEXT_FORBIDDEN"


def test_waiter_does_not_print_raw_connection_error(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["wait", "--operation-id", str(uuid4())])

    def fail(*a, **kw):
        raise RuntimeError("PRIVATE_DATABASE_URL")

    monkeypatch.setattr(waiter, "create_engine", fail)
    with pytest.raises(SystemExit):
        waiter.main()
    output = capsys.readouterr().out
    assert "PRIVATE_DATABASE_URL" not in output
    assert json.loads(output)["restart_handoff"] == "WAIT_FAILED"


def test_waiter_timeout_is_nonzero_and_cannot_authorize_restart(monkeypatch, capsys):
    from types import SimpleNamespace

    monkeypatch.setattr("sys.argv", ["wait", "--operation-id", str(uuid4())])
    monkeypatch.setattr(
        waiter, "create_engine", lambda *a, **kw: SimpleNamespace(dispose=lambda: None)
    )
    monkeypatch.setattr(waiter, "inspect_ready", lambda *a: ("WAITING_FOR_ORDER", {}))
    ticks = iter([0, 181])
    monkeypatch.setattr(waiter.time, "monotonic", lambda: next(ticks, 181))
    with pytest.raises(SystemExit) as caught:
        waiter.main()
    assert caught.value.code == 2
    assert json.loads(capsys.readouterr().out)["restart_handoff"] == "WAIT_TIMEOUT"
