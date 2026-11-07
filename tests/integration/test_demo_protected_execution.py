"""New production supervisor/child: real PG, OS SIGKILL and original natural lease."""

import asyncio
import json
import os
import signal
import subprocess
import sys
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from pydantic import SecretStr
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from integrations.binance_usdm.demo import DemoCredentials
from services.execution import demo_protected_worker_check as module
from tests.fixtures.demo_protected_execution_child import Transport
from tests.fixtures.demo_protected_worker import KEY, SECRET
from tests.integration.test_demo_protected_leader_probe import hold_row
from tests.integration.test_demo_protected_worker_sigkill import case  # noqa: F401


@pytest.mark.parametrize(
    "trigger_during_wait,restore_failure,contend",
    [(False, False, False), (True, False, False), (False, True, False), (False, False, True)],
)
def test_new_supervisor_real_sigkill_natural_lease_cleanup(
    case,  # noqa: F811
    trigger_during_wait,
    restore_failure,
    contend,
    monkeypatch,
):  # noqa: F811
    store, config, fake, endpoint, schema = case
    if restore_failure:

        async def fail_restore(*args, **kwargs):
            raise module.CheckBlocked("RECOVERY_SESSION_FAILED")

        monkeypatch.setattr(module, "restore", fail_restore)
    operation = uuid4()
    transport = Transport(endpoint)
    key = DemoCredentials(SecretStr(KEY), SecretStr(SECRET))
    parent = module.ScopedClient(
        config, key, send=transport, account="DEMO-test", operation=operation, entry_enabled=False
    )
    parent.sync_clock()
    initial_lease = store.acquire("DEMO-test", uuid4())
    c = module.DemoCoordinator(store, initial_lease, parent)
    c.refresh_metadata()
    c.connected()
    store.release(initial_lease)
    original = store.read("DEMO-test")
    children = []
    connections = []
    held_threads = []
    acquire = store.acquire

    def contended_acquire(account, owner):
        if contend and not held_threads:
            held_threads.append(hold_row(store, 2.6))
        return acquire(account, owner)

    monkeypatch.setattr(store, "acquire", contended_acquire)

    async def exercise():
        async def handler(socket):
            connections.append(True)
            await socket.wait_closed()

        async with serve(handler, "127.0.0.1", 0) as server:
            ws = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            env = {k: v for k, v in os.environ.items() if not k.startswith("BINANCE_")}
            env.update(
                TEST_ONLY_PROTECTED_WORKER="1",
                TEST_PROTECTED_DB_URL=store.engine.url.render_as_string(hide_password=False),
                TEST_PROTECTED_SCHEMA=schema,
                TEST_PROTECTED_CONFIG=config.model_dump_json(),
                TEST_PROTECTED_HTTP=endpoint,
                TEST_PROTECTED_WS=ws,
                PYTHONPATH=os.getcwd(),
            )

            class Worker:
                def __init__(self, owner):
                    self.p = subprocess.Popen(
                        [
                            sys.executable,
                            "-m",
                            "tests.fixtures.demo_protected_execution_child",
                            str(owner),
                            str(operation),
                        ],
                        env=env,
                        stdin=subprocess.PIPE,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                    )
                    self.pid = self.p.pid
                    children.append(self)

                def poll(self):
                    return self.p.poll()

                def wait(self, timeout=None):
                    return self.p.wait(timeout=timeout)

                def kill(self):
                    self.p.kill()
                    if trigger_during_wait:
                        order = store.read("DEMO-test").orders[str(operation)]
                        fake.trigger(order.tp_algo_id)

                def stop(self):
                    if self.p.stdin:
                        try:
                            self.p.stdin.write(b"S")
                            self.p.stdin.flush()
                        except BrokenPipeError:
                            pass
                        self.p.stdin.close()

            @asynccontextmanager
            async def connector(url):
                async with connect(ws) as socket:
                    yield socket

            try:
                return await module.run(
                    store, parent, operation, Worker, account="DEMO-test", connector=connector
                )
            finally:
                for child in children:
                    if child.poll() is None:
                        child.stop()
                        await asyncio.to_thread(child.wait, 120)
                    error = child.p.stderr.read()
                    assert not error, error.decode()

    result = asyncio.run(exercise())
    for thread in held_threads:
        thread.join(5)
    assert result["competing_leader_probe_attempts"] == (2 if contend else 1), result
    assert result["competing_leader_lock_timeouts"] == (1 if contend else 0), result
    assert result["prekill_failure_stage"] == "NONE", result
    assert result["kill_count"] == 1 and result["worker_exit_code"] == -signal.SIGKILL, result
    assert result["natural_lease_wait_ms"] + 250 >= result["remaining_at_wait_start_ms"] > 0
    assert result["lease_fence_after"] == result["lease_fence_before"] + 1
    assert result["flat_confirmation"] == "VERIFIED_FLAT", result
    assert result["leader_released"] and result["history_and_baselines_preserved"]
    assert result["parent_entry_post_attempts"] == result["parent_protection_post_attempts"] == 0
    assert len(connections) == (1 if restore_failure else 2)
    assert module.history_preserved(original, store.read("DEMO-test"), str(operation))
    entries = [
        p
        for m, path, p in fake.calls
        if m == "POST" and path == "/fapi/v1/order" and p.get("type") == "LIMIT"
    ]
    exits = [
        p
        for m, path, p in fake.calls
        if m == "POST" and path == "/fapi/v1/order" and p.get("reduceOnly") == "true"
    ]
    protects = [p for m, path, p in fake.calls if m == "POST" and path == "/fapi/v1/algoOrder"]
    assert len(entries) == 1 and len(protects) == 2
    assert len(exits) == (0 if trigger_during_wait else 1)
    if trigger_during_wait:
        assert result["protected_worker_check"] == "NOT_ACCEPTED_KEEP_PAUSED"
        assert not result["protected_continuity_observed"]
    elif restore_failure:
        assert result["protected_worker_check"] == "NOT_ACCEPTED_KEEP_PAUSED"
        assert result["failure"] == "RECOVERY_SESSION_FAILED"
    else:
        assert (
            result["protected_worker_check"]
            == "PROTECTED_CHILD_SIGKILL_FENCED_RECOVERY_FLAT_VERIFIED"
        ), result
        assert result["stale_effect_blocked"] and result["stale_renew_blocked"]
        assert result["same_ids_reconciled_in_new_session"]
    print("NEW_PROTECTED_WORKER_RESULT_JSON=" + json.dumps(result), flush=True)
