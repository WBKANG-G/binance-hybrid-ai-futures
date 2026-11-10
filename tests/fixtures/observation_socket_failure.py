"""Wrap the original local WSS harness; normal/historical fixture bytes stay fixed."""

import asyncio
import contextlib
import json
from contextlib import asynccontextmanager

from tests.fixtures import tp_candidate_session as original


async def observe_with_receiver_hook(scenario, monkeypatch, **kwargs):
    store, coordinator, fake, options = scenario
    serve = original.serve

    @asynccontextmanager
    async def hooked_serve(handler, host, port):
        async def hooked(socket):
            options["on_socket"](socket)

            async def wake_after_flat():
                while socket.close_code is None:
                    ledger = store.read(coordinator.lease.account_id)
                    order = ledger.orders.get(str(coordinator._own_operation))
                    if order is not None and order.position_state.value == "CLOSED":
                        await socket.send(
                            json.dumps(
                                {
                                    "e": "ACCOUNT_UPDATE",
                                    "E": coordinator.client.clock_ms(),
                                }
                            )
                        )
                        return
                    await asyncio.sleep(0.01)

            wake = asyncio.create_task(wake_after_flat())
            try:
                await handler(socket)
            finally:
                wake.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await wake

        async with serve(hooked, host, port) as server:
            yield server

    with monkeypatch.context() as local:
        local.setattr(original, "serve", hooked_serve)
        return await original.observe(scenario, monkeypatch, **kwargs)
