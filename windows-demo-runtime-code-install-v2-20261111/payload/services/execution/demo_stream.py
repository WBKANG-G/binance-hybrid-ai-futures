"""Private DEMO WSS supervision. Events invalidate state; REST establishes truth."""

import asyncio
import logging
import time
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import timedelta
from typing import Any, Protocol
from uuid import uuid4

from pydantic import SecretStr
from websockets.asyncio.client import connect

from integrations.binance_usdm.demo import WS_HOST, DemoFailure
from services.execution.demo import DemoCoordinator
from services.execution.demo_event_clock import validate_event_time_async
from services.execution.demo_event_inbox import EventInbox
from services.execution.demo_models import PrivateSessionDiagnostic
from services.execution.demo_session_diagnostics import event_info, failure_diagnostic


async def settled_call[T](function: Callable[..., T], *args: Any) -> T:
    """Drain an in-flight thread before cancellation starts another DB operation.

    Cancelling to_thread's awaiter does not stop the worker or release its row
    lock. Shield it and consume completion before the caller's finally cleanup.
    Repeated cancellation must not strand the worker either.
    """
    worker = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not worker.cancelled():
            worker.exception()  # Consume failure without logging any private details.
        raise


def entry_wait_seconds(coordinator: DemoCoordinator) -> float:
    """Bound idle receipt wait by the nearest outstanding entry expiry.

    This is scheduling only: expire/cancel still establishes durable intent and
    reconciles fills under the existing lease and fencing rules.
    """
    ledger = coordinator.store.read(coordinator.lease.account_id)
    now = coordinator.store.now()
    remaining = [
        (order.signal.expires_at - now).total_seconds()
        for order in ledger.orders.values()
        if order.remaining
    ]
    # Optional coordinator scheduling hook; defaults and all freshness/expiry
    # checks retain their original rules. Incoming hints wake take immediately.
    idle = float(getattr(coordinator, "idle_wait_seconds", lambda state: 5.0)(ledger))
    idle = max(0.001, min(15.0, idle))
    return max(0.001, min(idle, min(remaining, default=idle)))


class Connection(Protocol):
    async def recv(self) -> str | bytes: ...
    async def ping(self) -> Any: ...


def private_connect(url: SecretStr) -> AbstractAsyncContextManager[Connection]:
    value = url.get_secret_value()
    if not value.startswith(WS_HOST + "/private/ws/"):
        raise ValueError("DEMO private URL must remain isolated")
    logger = logging.Logger("demo-private-redacted")
    logger.disabled = True
    return connect(
        value,
        open_timeout=5,
        close_timeout=2,
        max_size=1_048_576,
        ping_interval=20,
        ping_timeout=10,
        logger=logger,
    )


async def session(
    coordinator: DemoCoordinator,
    *,
    connector: Callable[[SecretStr], AbstractAsyncContextManager[Connection]] = private_connect,
    on_ready: Callable[[], None] | None = None,
    max_messages: int | None = None,
) -> None:
    """One session. Caller may restart after backoff; a reconnect always reconciles first."""
    store, lease, client = coordinator.store, coordinator.lease, coordinator.client
    coordinator.session_failed = False
    coordinator.session_diagnostic = PrivateSessionDiagnostic(stage="SESSION_CREATE")
    try:
        key = store.change(lease, "DEMO_SESSION_CREATE", lambda ledger: client.listen_key("POST"))
        if key is None:
            raise DemoFailure("DEMO private session unavailable")
    except Exception as error:
        coordinator.session_failed = True
        coordinator.session_diagnostic = failure_diagnostic(error, coordinator.session_diagnostic)
        try:
            store.change(
                lease,
                "DEMO_PRIVATE_SESSION_FAILED",
                lambda ledger: setattr(
                    ledger,
                    "last_private_session_failure",
                    coordinator.session_diagnostic.model_copy(deep=True),
                ),
            )
        except Exception:
            pass
        raise DemoFailure("DEMO private connection/session failed; entries blocked") from None
    renewed = store.now()
    inbox: EventInbox | None = None
    reader: asyncio.Task[None] | None = None
    failure_recorded = False

    def receive_failed() -> None:
        coordinator.session_failed = True

    def metrics() -> None:
        if inbox is not None:
            coordinator.session_diagnostic.received_events = inbox.received
            coordinator.session_diagnostic.coalesced_events = inbox.coalesced

    async def record_failure(error: Exception) -> None:
        nonlocal failure_recorded
        coordinator.session_failed = True
        callback_ms = coordinator.session_diagnostic.ready_callback_ms
        if inbox is not None and inbox.failure is not None:
            coordinator.session_diagnostic = inbox.failure.model_copy(deep=True)
            coordinator.session_diagnostic.ready_callback_ms = callback_ms
        else:
            coordinator.session_diagnostic = failure_diagnostic(
                error, coordinator.session_diagnostic
            )
        metrics()
        failure_recorded = True
        try:
            await settled_call(
                store.change,
                lease,
                "DEMO_PRIVATE_SESSION_FAILED",
                lambda ledger: setattr(
                    ledger,
                    "last_private_session_failure",
                    coordinator.session_diagnostic.model_copy(deep=True),
                ),
            )
        except Exception:
            pass

    try:
        coordinator.session_diagnostic.stage = "CONNECT"
        async with connector(client.private_url(key)) as websocket:
            inbox = EventInbox(
                lambda: int(store.now().timestamp() * 1000),
                receive_failed,
                validate_time=lambda stamp, detail: validate_event_time_async(
                    client, stamp, detail
                ),
            )
            reader = asyncio.create_task(inbox.run(websocket))
            try:
                # Begin receiving before slow REST setup or an entry callback.
                await asyncio.sleep(0)
                inbox.raise_if_failed()
                coordinator.session_diagnostic.stage = "RECONCILE"
                await settled_call(coordinator.connected)
                inbox.raise_if_failed()
                if on_ready:
                    await settled_call(store.renew, lease)
                    await settled_call(client.sync_clock)
                    inbox.raise_if_failed()
                    coordinator.session_diagnostic.stage = "READY_CALLBACK"
                    callback_start = time.monotonic()
                    try:
                        await settled_call(on_ready)
                    finally:
                        coordinator.session_diagnostic.ready_callback_ms = min(
                            3600000, max(0, int((time.monotonic() - callback_start) * 1000))
                        )
                    inbox.raise_if_failed()
                coordinator.session_diagnostic.stage = "EXPIRY"
                await settled_call(coordinator.expire)
                inbox.raise_if_failed()
                count = 0
                while max_messages is None or count < max_messages:
                    coordinator.session_diagnostic.stage = "HEARTBEAT"
                    await settled_call(store.renew, lease)
                    await settled_call(client.sync_clock)
                    inbox.raise_if_failed()
                    if store.now() - renewed >= timedelta(minutes=25):
                        coordinator.session_diagnostic.stage = "KEY_RENEW"
                        await settled_call(
                            store.change,
                            lease,
                            "DEMO_SESSION_RENEW",
                            lambda ledger: client.listen_key("PUT"),
                        )
                        renewed = store.now()
                        inbox.raise_if_failed()
                    coordinator.session_diagnostic.stage = "EXPIRY"
                    await settled_call(coordinator.expire)
                    inbox.raise_if_failed()
                    wait_seconds = await settled_call(entry_wait_seconds, coordinator)
                    inbox.raise_if_failed()
                    try:
                        coordinator.session_diagnostic.stage = "RECEIVE"
                        coordinator.session_diagnostic.event_kind = "NONE"
                        coordinator.session_diagnostic.event_age_ms = None
                        coordinator.session_diagnostic.event_receive_age_ms = None
                        coordinator.session_diagnostic.event_clock_wait_ms = 0
                        hint = await inbox.take(timeout=wait_seconds)
                        coordinator.session_diagnostic.stage = "EVENT"
                        (
                            coordinator.session_diagnostic.event_kind,
                            coordinator.session_diagnostic.event_age_ms,
                        ) = event_info(hint.event(), int(store.now().timestamp() * 1000))
                        coordinator.session_diagnostic.event_receive_age_ms = hint.receive_age_ms
                        coordinator.session_diagnostic.event_clock_wait_ms = hint.clock_wait_ms
                        metrics()
                        # Still checks original E against NOW and establishes
                        # strict REST truth. Never rewrites/relaxes an old hint.
                        await settled_call(coordinator.event, hint.event())
                        count += 1
                        inbox.raise_if_failed()
                    except TimeoutError:
                        inbox.raise_if_failed()
                        coordinator.session_diagnostic.stage = "EXPIRY"
                        await settled_call(coordinator.expire)
                        inbox.raise_if_failed()
                        coordinator.session_diagnostic.stage = "PING"
                        pong = await websocket.ping()
                        await asyncio.wait_for(pong, timeout=5)
                        inbox.raise_if_failed()
                        await settled_call(coordinator.connected)
                        inbox.raise_if_failed()
                    coordinator.session_diagnostic.stage = "EXPIRY"
                    await settled_call(coordinator.expire)
                    inbox.raise_if_failed()
            finally:
                # Only the receiver reads the socket; stop it before intentional
                # socket closure so closure cannot become a fabricated failure.
                current_task = asyncio.current_task()
                cancellations = current_task.cancelling() if current_task is not None else 0
                reader.cancel()
                while not reader.done():
                    try:
                        await asyncio.shield(reader)
                    except asyncio.CancelledError:
                        continue
                if not reader.cancelled():
                    reader.exception()
                metrics()
                if current_task is not None and current_task.cancelling() > cancellations:
                    raise asyncio.CancelledError
    except Exception as error:
        await record_failure(error)
        try:
            await settled_call(coordinator.disconnect)
        except Exception:
            pass
        raise DemoFailure("DEMO private connection/session failed; entries blocked") from None
    finally:
        # Validation cancellation must not hide a receiver failure observed
        # while a DB worker was draining. Capture before normal cleanup.
        if inbox is not None and inbox.failure is not None and not failure_recorded:
            await record_failure(DemoFailure("DEMO private receiver failed; entries blocked"))
        # Session closure never cancels SL/TP. Fencing prevents a stale worker deleting a new leader's key.
        try:
            await settled_call(client.sync_clock)
            await settled_call(coordinator.command, uuid4(), "PAUSE_ENTRIES")
        except Exception:
            pass
        try:
            await settled_call(coordinator.disconnect)
            await settled_call(
                store.change,
                lease,
                "DEMO_SESSION_CLOSE",
                lambda ledger: client.listen_key("DELETE"),
            )
        except Exception:
            pass
