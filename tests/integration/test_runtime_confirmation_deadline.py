"""Scheduler-only deadline checks; no synthetic flat proof is fabricated."""

# ruff: noqa: F811
import asyncio
from decimal import Decimal
from types import SimpleNamespace

import pytest

from integrations.binance_usdm.demo_tp_window_budget import ProofDeferred
from integrations.binance_usdm.rest import RESTResponse
from services.execution import demo_tp_budget_integration as legacy
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_managed_order_quota import managed  # noqa: F401
from tests.integration.test_managed_runtime_assembly import assembly  # noqa: F401
from tests.integration.test_runtime_header_provenance import prepared


def scheduler(assembly, monkeypatch, scenario):
    def send(method, path, query, key):
        return RESTResponse(200, b"{}", {"x-mbx-used-weight-1m": "600"})

    session, b = prepared(assembly, send)
    c = session.coordinator
    state = dict(now=0.0, calls=[], sleeps=[])
    amounts = {
        "renew": 120,
        "refresh": 120,
        "position": 120,
        "proof": 120,
        "defer": 119,
        "clip": 119.5,
    }

    def stage(name):
        state["calls"].append(name)
        if (
            (name == "renew" and scenario in {"renew", "defer", "clip"})
            or (name == "clock" and scenario == "refresh")
            or (name == "position" and scenario == "position")
            or (name == "proof" and scenario == "proof")
        ):
            state["now"] += amounts[scenario]

    def clock():
        return state["now"]

    async def pause(delay):
        stage("sleep")
        state["sleeps"].append(delay)
        state["now"] += delay
        await asyncio.sleep(0)

    def physical():
        stage("position")
        b.flat_observed = True
        return Decimal(0), Decimal(0)

    def require(cost):
        stage("admit")
        if scenario in {"defer", "clip"}:
            raise ProofDeferred

    def confirm():
        stage("proof")
        return "UNKNOWN"

    monkeypatch.setattr(legacy, "time", SimpleNamespace(monotonic=clock))
    monkeypatch.setattr(
        legacy, "asyncio", SimpleNamespace(to_thread=asyncio.to_thread, sleep=pause)
    )
    monkeypatch.setattr(c.store, "renew", lambda lease: stage("renew"))
    monkeypatch.setattr(c.client, "sync_clock", lambda: stage("clock"))
    monkeypatch.setattr(c, "_physical_position", physical)
    monkeypatch.setattr(c, "confirm_flat", confirm)
    monkeypatch.setattr(b, "require_proof", require)
    monkeypatch.setattr(b, "_fresh", lambda now: scenario != "refresh")
    b.flat_observed = scenario != "position"
    c._confirmation_clock = clock
    c._confirmation_sleep = pause
    return session, c, state


@pytest.mark.parametrize(
    "scenario,expected",
    [
        ("renew", ["renew"]),
        ("refresh", ["renew", "clock"]),
        ("position", ["renew", "position"]),
        ("proof", ["renew", "admit", "proof"]),
        ("defer", ["renew", "position", "admit", "sleep"]),
        ("clip", ["renew", "position", "admit", "sleep"]),
    ],
)
def test_no_new_confirmation_phase_starts_after_admission_deadline(
    assembly, monkeypatch, scenario, expected
):
    session, c, state = scheduler(assembly, monkeypatch, scenario)
    try:
        assert asyncio.run(c.confirm_exit_budgeted()) == "UNKNOWN"
        assert state["calls"] == expected
        if scenario == "clip":
            assert state["sleeps"] == [0.5]
        assert c.budgeted_flat_confirmation == "UNKNOWN"
    finally:
        session.close()


def test_four_unknown_results_stop_without_manufacturing_flatness(assembly, monkeypatch):
    session, c, state = scheduler(assembly, monkeypatch, "normal")
    try:
        assert asyncio.run(c.confirm_exit_budgeted()) == "UNKNOWN"
        assert state["calls"].count("proof") == 4 and len(state["sleeps"]) == 3
        assert c.confirmation_attempts_completed == 4
        assert c.confirmation_diagnostics()["stop_reason"] == "ATTEMPT_LIMIT_REACHED"
        assert c.budgeted_flat_confirmation == "UNKNOWN"
    finally:
        session.close()


def test_stalled_clock_still_has_a_finite_deferral_limit(assembly, monkeypatch):
    session, c, state = scheduler(assembly, monkeypatch, "defer")
    c._confirmation_clock = lambda: 0.0

    async def pause(delay):
        state["sleeps"].append(delay)
        await asyncio.sleep(0)

    c._confirmation_sleep = pause
    try:
        assert asyncio.run(c.confirm_exit_budgeted()) == "UNKNOWN"
        assert c.confirmation_deferrals == 120 and len(state["sleeps"]) == 119
        assert "proof" not in state["calls"] and c.confirmation_attempts_completed == 0
        assert c.confirmation_diagnostics()["stop_reason"] == "DEFERRAL_LIMIT_REACHED"
    finally:
        session.close()


def test_scheduler_clock_regression_stops_before_another_phase(assembly, monkeypatch):
    session, c, state = scheduler(assembly, monkeypatch, "normal")

    def renew(lease):
        state["calls"].append("renew")
        state["now"] = -1.0

    monkeypatch.setattr(c.store, "renew", renew)
    try:
        assert asyncio.run(c.confirm_exit_budgeted()) == "UNKNOWN"
        assert state["calls"] == ["renew"]
        assert c.confirmation_diagnostics()["stop_reason"] == "CLOCK_UNTRUSTED"
    finally:
        session.close()


def test_slow_cost_check_cannot_start_proof_after_deadline(assembly, monkeypatch):
    session, c, state = scheduler(assembly, monkeypatch, "normal")

    def require(cost):
        state["calls"].append("admit")
        state["now"] = 120.0

    monkeypatch.setattr(c.admission_budget, "require_proof", require)
    try:
        assert asyncio.run(c.confirm_exit_budgeted()) == "UNKNOWN"
        assert state["calls"] == ["renew", "admit"]
        assert c.confirmation_attempts_completed == 0
    finally:
        session.close()


def test_remaining_exposure_returns_unknown_without_waiting_for_financial_proof(
    assembly, monkeypatch
):
    session, c, state = scheduler(assembly, monkeypatch, "normal")
    c.admission_budget.flat_observed = False

    def position():
        state["calls"].append("position")
        return Decimal("0.0007"), Decimal("100")

    monkeypatch.setattr(c, "_physical_position", position)
    try:
        assert asyncio.run(c.confirm_exit_budgeted()) == "UNKNOWN"
        assert state["calls"] == ["renew", "position"] and not state["sleeps"]
        assert c.confirmation_diagnostics()["stop_reason"] == "EXPOSURE_REMAINS"
    finally:
        session.close()


def test_original_operation_failure_is_rethrown_without_retry(assembly, monkeypatch):
    session, c, state = scheduler(assembly, monkeypatch, "normal")

    class OriginalFailure(Exception):
        pass

    def renew(lease):
        state["calls"].append("renew")
        raise OriginalFailure

    monkeypatch.setattr(c.store, "renew", renew)
    try:
        with pytest.raises(OriginalFailure):
            asyncio.run(c.confirm_exit_budgeted())
        assert state["calls"] == ["renew"]
        assert c.confirmation_diagnostics()["stop_reason"] == "ORIGINAL_OPERATION_FAILED"
    finally:
        session.close()


def test_caller_cancellation_is_not_reported_as_a_flat_proof(assembly, monkeypatch):
    session, c, state = scheduler(assembly, monkeypatch, "normal")

    async def cancelled(delay):
        raise asyncio.CancelledError

    c._confirmation_sleep = cancelled
    try:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(c.confirm_exit_budgeted())
        assert state["calls"] == ["renew", "admit", "proof"]
        assert c.budgeted_flat_confirmation == "UNKNOWN"
        assert c.confirmation_diagnostics()["stop_reason"] == "CALLER_CANCELLED"
    finally:
        session.close()
