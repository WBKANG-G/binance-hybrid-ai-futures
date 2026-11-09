"""Local WSS only; original Capture and session perform all frame validation."""

import asyncio
import json
from contextlib import asynccontextmanager
from uuid import uuid4

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from services.execution import demo_tp_trigger_check as module
from services.execution import demo_validation as validation
from services.execution.demo_tp_admission_candidate import run_admission_candidate


async def observe(scenario, monkeypatch, *, profile, duration, capture):
    store, c, fake, options = scenario
    operation = uuid4()
    triggered = False
    sent_initial = False

    async def handler(socket):
        nonlocal triggered, sent_initial
        try:
            while True:
                if socket.close_code is not None:
                    return
                ledger = store.read("DEMO-test")
                order = ledger.orders.get(str(operation))
                just_triggered = False
                if (
                    not triggered
                    and options["trigger"]
                    and order
                    and ledger.validation_evidence.get(str(operation), {}).get("target_verified")
                ):
                    fake.trigger(order.tp_algo_id)
                    triggered = True
                    just_triggered = True
                if profile in {"burst", "pulse"} or not sent_initial or just_triggered:
                    await socket.send(json.dumps({"e": "ACCOUNT_UPDATE", "E": c.client.clock_ms()}))
                    sent_initial = True
                await asyncio.sleep(
                    0.02 if profile == "burst" else 0.25 if profile == "pulse" else 0.05
                )
        except Exception:
            pass

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]

        @asynccontextmanager
        async def connector(url):
            async with connect(f"ws://127.0.0.1:{port}") as socket:
                yield socket

        async def local_session(coordinator, *, on_ready):
            await capture.supervise(coordinator, connector=connector, on_ready=on_ready)

        monkeypatch.setattr(validation, "session", local_session)
        if duration != 300:
            monkeypatch.setattr(module, "OBSERVATION_SECONDS", duration)
        else:
            assert module.OBSERVATION_SECONDS == 300
        return await run_admission_candidate(c, operation)
