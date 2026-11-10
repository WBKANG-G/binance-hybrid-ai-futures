"""Real blocked workers must settle before cancellation permits runtime cleanup."""

# ruff: noqa: F811
import asyncio
from decimal import Decimal
from threading import Event

import pytest

from packages.persistence.demo_order_quota import LocalQuotaStopped
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_managed_order_quota import managed  # noqa: F401
from tests.integration.test_managed_runtime_assembly import assembly  # noqa: F401
from tests.integration.test_runtime_confirmation_deadline import scheduler


def blocked(assembly, monkeypatch, stage):
    session, c, state = scheduler(assembly, monkeypatch, "normal")
    entered, release, finished = Event(), Event(), Event()

    def work(*args):
        state["calls"].append(stage)
        entered.set()
        try:
            assert release.wait(5)
            if stage == "position":
                return Decimal(0), Decimal(0)
            return "UNKNOWN" if stage == "proof" else None
        finally:
            finished.set()

    if stage == "renew":
        monkeypatch.setattr(c.store, "renew", work)
    elif stage == "clock":
        monkeypatch.setattr(c.admission_budget, "_fresh", lambda now: False)
        monkeypatch.setattr(c.client, "sync_clock", work)
    elif stage == "position":
        c.admission_budget.flat_observed = False
        monkeypatch.setattr(c, "_physical_position", work)
    else:
        monkeypatch.setattr(c, "confirm_flat", work)
    return session, c, state, entered, release, finished


@pytest.mark.parametrize("stage", ["renew", "clock", "position", "proof"])
def test_cancel_keeps_confirmation_and_journal_owned_until_worker_finishes(
    assembly, monkeypatch, stage
):
    session, c, state, entered, release, finished = blocked(assembly, monkeypatch, stage)

    async def run():
        task = asyncio.create_task(c.confirm_exit_budgeted())
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(task), 0.1)
            assert (
                not finished.is_set() and c.confirmation_diagnostics()["confirmation_call_active"]
            )
            with pytest.raises(LocalQuotaStopped, match="CONFIRMATION_RUNNING"):
                session.close()
            assert not session.budget.revoked and session.journal.owns_process_lock()
            assert await c.confirm_exit_budgeted() == "UNKNOWN"
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert (
                finished.is_set() and not c.confirmation_diagnostics()["confirmation_call_active"]
            )
            assert c.confirmation_stop_reason == "CALLER_CANCELLED"
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    try:
        asyncio.run(run())
    finally:
        release.set()
        session.close()


def test_close_rejects_an_active_direct_confirmation_before_revoking_resources(
    assembly, monkeypatch
):
    session, c, state, entered, release, finished = blocked(assembly, monkeypatch, "renew")

    async def run():
        task = asyncio.create_task(c.confirm_exit_budgeted())
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            with pytest.raises(LocalQuotaStopped, match="CONFIRMATION_RUNNING"):
                session.close()
            assert not session.budget.revoked and session.journal.owns_process_lock()
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    try:
        asyncio.run(run())
    finally:
        release.set()
        session.close()


def test_closed_runtime_cannot_start_its_first_confirmation(assembly, monkeypatch):
    session, c, state = scheduler(assembly, monkeypatch, "normal")
    session.close()
    assert asyncio.run(c.confirm_exit_budgeted()) == "UNKNOWN"
    assert not state["calls"] and not state["sleeps"]


@pytest.mark.parametrize("stage", ["renew", "proof"])
def test_repeated_cancel_drains_worker_failure_before_releasing_ownership(
    assembly, monkeypatch, stage
):
    session, c, state, entered, release, finished = blocked(assembly, monkeypatch, stage)

    class WorkerFailure(Exception):
        pass

    def failing(*args):
        entered.set()
        try:
            assert release.wait(5)
            raise WorkerFailure("synthetic detail not logged")
        finally:
            finished.set()

    monkeypatch.setattr(
        c.store if stage == "renew" else c, "renew" if stage == "renew" else "confirm_flat", failing
    )

    async def run():
        task = asyncio.create_task(c.confirm_exit_budgeted())
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            for _ in range(2):
                task.cancel()
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(asyncio.shield(task), 0.05)
                assert (
                    not finished.is_set()
                    and c.confirmation_diagnostics()["confirmation_call_active"]
                )
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert finished.is_set()
            assert c.confirmation_stop_reason == "CALLER_CANCELLED"
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    try:
        asyncio.run(run())
    finally:
        release.set()
        session.close()


def test_runtime_run_keeps_settings_and_journal_owned_while_cancellation_drains(
    assembly, monkeypatch
):
    from uuid import uuid4

    from services.execution import demo_managed_runtime as runtime
    from tests.integration.test_managed_runtime_assembly import open_session

    session = open_session(assembly, allow_synthetic_model_execution=True)
    assembly[3].seconds += 70.01
    c = session.coordinator
    entered, release, finished = Event(), Event(), Event()

    def renew(lease):
        entered.set()
        try:
            assert release.wait(5)
        finally:
            finished.set()

    monkeypatch.setattr(c.store, "renew", renew)

    async def owned(coordinator, operation):
        return {"confirmation": await coordinator.confirm_exit_budgeted()}

    monkeypatch.setattr(runtime, "run_managed_quota_candidate", owned)

    async def run():
        task = asyncio.create_task(session.run(uuid4()))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(task), 0.1)
            assert c.client.settings.demo_trading_enabled and not finished.is_set()
            assert session.journal.owns_process_lock()
            with pytest.raises(LocalQuotaStopped, match="STILL_RUNNING"):
                session.close()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert finished.is_set() and not c.client.settings.demo_trading_enabled
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    try:
        asyncio.run(run())
    finally:
        release.set()
        session.close()
