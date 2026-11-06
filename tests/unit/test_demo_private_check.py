"""Bounded private checks never submit an entry and reject occupied accounts."""

import asyncio
from types import SimpleNamespace

import pytest

import scripts.run_demo as cli
from integrations.binance_usdm.demo import DemoFailure


def setup(occupied=False):
    state = SimpleNamespace(
        reconciled=True,
        risk_state=SimpleNamespace(value="NORMAL"),
        positions={"BTCUSDT": int(occupied)},
        orders={},
        stream_ready=True,
    )
    return SimpleNamespace(
        store=SimpleNamespace(read=lambda account: state),
        lease=SimpleNamespace(account_id="DEMO-test"),
    )


def test_private_check_observes_then_cancels_session(monkeypatch, capsys):
    closed = []

    async def fake_session(c, *, on_ready):
        try:
            on_ready()
            await asyncio.Future()
        finally:
            closed.append(True)

    monkeypatch.setattr(cli, "session", fake_session)
    asyncio.run(cli.check_private(setup(), seconds=0.02))
    assert closed == [True]
    output = capsys.readouterr().out
    assert "CONNECTED" in output and "OBSERVATION_FINISHED" in output
    assert '"entry_signal_supplied": false' in output


def test_private_check_rejects_occupied_account_before_session(monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("session must not start")

    monkeypatch.setattr(cli, "session", forbidden)
    with pytest.raises(DemoFailure, match="empty reconciled"):
        asyncio.run(cli.check_private(setup(occupied=True), seconds=0.02))


def test_private_check_timeout_without_readiness_is_failure(monkeypatch):
    async def stalled(*args, **kwargs):
        await asyncio.Future()

    monkeypatch.setattr(cli, "session", stalled)
    with pytest.raises(DemoFailure, match="before readiness"):
        asyncio.run(cli.check_private(setup(), seconds=0.02))


def test_private_check_connection_error_is_not_success(monkeypatch, capsys):
    async def broken(c, *, on_ready):
        on_ready()
        raise DemoFailure("synthetic disconnect")

    monkeypatch.setattr(cli, "session", broken)
    with pytest.raises(DemoFailure):
        asyncio.run(cli.check_private(setup(), seconds=0.02))
    assert "OBSERVATION_FINISHED" not in capsys.readouterr().out
