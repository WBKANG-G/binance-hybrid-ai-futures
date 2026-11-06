"""Real PG/core execution paths: unfilled/partial/protection failure never armed."""

import asyncio
import threading
from contextlib import asynccontextmanager
from decimal import Decimal
from uuid import uuid4

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from services.execution import demo_protected_worker_check as module
from tests.integration.test_demo import posts
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_tp_trigger_check import case  # noqa: F401


@pytest.mark.parametrize(
    "mode",
    [
        "unfilled",
        "partial",
        "stop_failure",
        "parent_lost",
        "unresolved_exit",
        "parent_lost_after_partial",
        "protection_changed_before_kill",
        "probe_inconclusive",
    ],
)
def test_unarmed_child_graceful_cleanup_without_kill_or_second_entry(case, mode, monkeypatch):  # noqa: F811
    store, old, fake, options = case
    options["fill"] = mode in {
        "stop_failure",
        "protection_changed_before_kill",
        "probe_inconclusive",
    }
    options["trigger"] = False
    if mode == "unresolved_exit":
        fake.defer_exit = True
    if mode == "stop_failure":
        fake.reject_stop = True
    send = old.client.send

    def transport(method, path, query, key):
        from urllib.parse import parse_qs

        response = send(method, path, query, key)
        if (
            mode in {"partial", "unresolved_exit", "parent_lost_after_partial"}
            and method == "POST"
            and path == "/fapi/v1/order"
        ):
            p = {k: v[0] for k, v in parse_qs(query.get_secret_value()).items()}
            if p.get("type") == "LIMIT":
                fake.fill(p["newClientOrderId"], Decimal("0.0003"), Decimal("80000"))
        return response

    operation = uuid4()
    store.release(old.lease)

    def make(entry):
        return module.ScopedClient(
            old.client.settings,
            old.client.credentials,
            send=transport,
            clock_ms=old.client.clock_ms,
            account="DEMO-test",
            operation=operation,
            entry_enabled=entry,
        )

    parent = make(False)
    parent.sync_clock()
    children = []
    parent_proofs = []
    original_protected = module.protected

    def checked_protected(store, client, account, operation, **kwargs):
        if not client.entry_enabled:
            parent_proofs.append(True)
            if mode == "protection_changed_before_kill" and len(parent_proofs) == 2:
                raise module.CheckBlocked("SIGNED_PROTECTION_CHANGED")
        return original_protected(store, client, account, operation, **kwargs)

    async def inconclusive_probe(*args, **kwargs):
        raise module.CheckBlocked("COMPETING_LEADER_PROBE_BUSY")

    monkeypatch.setattr(module, "protected", checked_protected)
    if mode == "probe_inconclusive":
        monkeypatch.setattr(module, "reject_competing_leader", inconclusive_probe)

    async def exercise():
        async def handler(socket):
            await socket.wait_closed()

        async with serve(handler, "127.0.0.1", 0) as server:
            url = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"

            @asynccontextmanager
            async def connector(key):
                async with connect(url) as socket:
                    yield socket

            class TaskChild:
                pid = 0

                def __init__(self, owner):
                    self.stopped = mode == "parent_lost"
                    self.exit_code = None
                    self.done = threading.Event()
                    self.kill_calls = 0
                    self.c = None

                    async def start():
                        lease = store.acquire("DEMO-test", owner)
                        c = module.CappedCoordinator(store, lease, make(True))
                        self.c = c
                        c.client.sync_clock()
                        c.refresh_metadata()
                        try:
                            await module.child_run(
                                c,
                                operation,
                                stop=lambda: (
                                    self.stopped
                                    or (
                                        mode == "parent_lost_after_partial"
                                        and bool(
                                            store.read("DEMO-test").orders.get(str(operation))
                                            and store.read("DEMO-test")
                                            .orders[str(operation)]
                                            .filled
                                        )
                                    )
                                ),
                                connector=connector,
                                ready_timeout=0.4,
                                lifetime=2,
                            )
                            self.exit_code = 0
                        except Exception:
                            self.exit_code = 2
                        finally:
                            self.done.set()

                    self.task = asyncio.create_task(start())
                    children.append(self)

                def poll(self):
                    return self.exit_code

                def stop(self):
                    self.stopped = True

                def kill(self):
                    self.kill_calls += 1
                    raise AssertionError("Unprotected child must not be killed")

                def wait(self, timeout=None):
                    assert self.done.wait(timeout)
                    return self.exit_code

            return await module.run(
                store,
                parent,
                operation,
                TaskChild,
                account="DEMO-test",
                connector=connector,
                startup_timeout=3,
            )

    result = asyncio.run(exercise())
    assert result["protected_worker_check"] == "NOT_ACCEPTED_KEEP_PAUSED", result
    if mode == "protection_changed_before_kill":
        assert result["prekill_failure_stage"] == "FINAL_SIGNED_PROTECTION_CHECK", result
        assert result["failure"] == "SIGNED_PROTECTION_CHANGED", result
        assert result["competing_leader_blocked"], result
    elif mode == "probe_inconclusive":
        assert result["prekill_failure_stage"] == "COMPETING_LEADER_PROBE", result
        assert result["failure"] == "COMPETING_LEADER_PROBE_BUSY", result
        assert not result["competing_leader_blocked"], result
    assert result["kill_count"] == 0 and all(c.kill_calls == 0 for c in children)
    assert result["leader_released"], result
    assert store.read("DEMO-test").paused
    if mode == "unresolved_exit":
        assert result["flat_confirmation"] == "UNKNOWN" and any(fake.positions.values()), result
    else:
        assert result["flat_confirmation"] == "VERIFIED_FLAT" and not any(
            fake.positions.values()
        ), result
    assert len([p for p in posts(fake) if p["type"] == "LIMIT"]) == (
        0 if mode == "parent_lost" else 1
    )
    assert len([p for p in posts(fake) if p.get("reduceOnly") == "true"]) <= 1
    assert parent.entry_attempts == 0
    with pytest.raises(module.CheckBlocked, match="OPERATION_ALREADY_USED"):
        module.fresh_start(store.read("DEMO-test"), operation)


def test_child_exits_before_acquire_no_kill_or_order_and_parent_checks_flat(case):  # noqa: F811
    store, old, fake, _ = case
    store.release(old.lease)
    operation = uuid4()
    client = module.ScopedClient(
        old.client.settings,
        old.client.credentials,
        send=old.client.send,
        clock_ms=old.client.clock_ms,
        account="DEMO-test",
        operation=operation,
        entry_enabled=False,
    )
    client.sync_clock()

    class ExitedChild:
        pid = 0

        def poll(self):
            return 12

        def wait(self, timeout=None):
            return 12

        def stop(self):
            pytest.fail("already exited")

        def kill(self):
            pytest.fail("must not kill")

    async def exercise():
        async def handler(socket):
            await socket.wait_closed()

        async with serve(handler, "127.0.0.1", 0) as server:
            url = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"

            @asynccontextmanager
            async def connector(key):
                async with connect(url) as socket:
                    yield socket

            return await module.run(
                store,
                client,
                operation,
                lambda owner: ExitedChild(),
                account="DEMO-test",
                connector=connector,
            )

    result = asyncio.run(exercise())
    assert result["failure"] == "CHILD_EXITED_BEFORE_PROTECTED_READY"
    assert result["kill_count"] == 0 and result["flat_confirmation"] == "VERIFIED_FLAT"
    assert not posts(fake) and not store.read("DEMO-test").orders
