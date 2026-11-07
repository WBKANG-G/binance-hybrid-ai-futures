"""A deadline or repeated caller cancellation must not strand session cleanup."""

import asyncio

import pytest

from services.execution.demo_open_disconnect_check import drain_session


def test_caller_cancellation_waits_for_session_cleanup_and_propagates():
    async def exercise():
        entered = asyncio.Event()
        release = asyncio.Event()
        finished = False

        async def session_cleanup():
            nonlocal finished
            entered.set()
            await release.wait()
            finished = True

        child = asyncio.create_task(session_cleanup())
        await entered.wait()
        parent = asyncio.create_task(drain_session(child))
        await asyncio.sleep(0)
        parent.cancel()
        await asyncio.sleep(0)
        parent.cancel()
        await asyncio.sleep(0)
        assert not parent.done() and not child.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await parent
        assert child.done() and not child.cancelled() and finished

    asyncio.run(exercise())


def test_cancel_once_does_not_interrupt_finally_on_repeated_caller_cancel():
    async def exercise():
        entered = asyncio.Event()
        cleanup = asyncio.Event()
        release = asyncio.Event()
        finished = False

        async def supervised():
            nonlocal finished
            try:
                entered.set()
                await asyncio.Future()
            finally:
                cleanup.set()
                await release.wait()
                finished = True

        child = asyncio.create_task(supervised())
        await entered.wait()
        parent = asyncio.create_task(drain_session(child, cancel=True))
        await cleanup.wait()
        parent.cancel()
        await asyncio.sleep(0)
        parent.cancel()
        await asyncio.sleep(0)
        assert not parent.done() and not child.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await parent
        assert child.done() and child.cancelled() and finished

    asyncio.run(exercise())


def test_session_exception_is_consumed_without_raw_output(capsys):
    async def exercise():
        async def failed():
            raise OSError("PRIVATE_URL_NEVER_PRINT")

        error = await drain_session(asyncio.create_task(failed()))
        assert isinstance(error, OSError)

    asyncio.run(exercise())
    assert capsys.readouterr().out == ""
