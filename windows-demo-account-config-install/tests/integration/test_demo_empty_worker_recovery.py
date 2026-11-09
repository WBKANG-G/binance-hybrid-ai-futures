"""Real subprocess SIGKILL + real PostgreSQL; localhost WSS and synthetic SDK."""

import asyncio
import json
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from scripts.observe_demo_private import ObservationClient
from services.execution import demo_empty_worker_recovery as module
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401


@pytest.fixture
def case(preview_case):  # noqa: F811
    store, c, fake, _, _ = preview_case
    source = c.client
    client = ObservationClient(
        source.settings.model_copy(update={"demo_trading_enabled": True}),
        source.credentials,
        send=source.send,
        clock_ms=source.clock_ms,
    )
    client.sync_clock()
    store.change(c.lease, "TEST_WORKER_PAUSE", lambda state: setattr(state, "paused", True))
    store.release(c.lease)
    return store, client, fake


async def exercise(
    case, tmp_path, *, advance=True, lease_timeout=1, patch=None, reconnect_close=False
):
    store, client, fake = case
    operation = uuid4()
    with store.engine.connect() as conn:
        schema = conn.execute(text("SHOW search_path")).scalar_one()
    child_path = tmp_path / "worker.py"
    child_path.write_text("""
import asyncio,sys
from uuid import UUID
from sqlalchemy import create_engine
from pydantic import SecretStr
from contextlib import asynccontextmanager
from websockets.asyncio.client import connect
from integrations.binance_usdm.demo import DemoClient,DemoCredentials
from packages.persistence.demo import DemoStore
from scripts.observe_demo_private import ObservationClient
from services.execution.demo import DemoCoordinator
from services.execution.demo_empty_worker_recovery import worker
from tests.fixtures.demo import FakeDemo
from tests.integration.test_demo import NOW,settings
from tests.integration.test_demo_isolated import configure
engine=create_engine(sys.argv[1],connect_args={"options":"-csearch_path="+sys.argv[2]})
fake=FakeDemo(NOW)
store=DemoStore(engine,clock=lambda:fake.at)
lease=store.acquire("DEMO-test",UUID(sys.argv[3]))
client=DemoClient(settings().model_copy(update={"margin_mode":"ISOLATED","symbols":("BTCUSDT",)}),DemoCredentials(SecretStr("synthetic-key"),SecretStr("synthetic-secret")),send=fake,clock_ms=lambda:int(fake.at.timestamp()*1000))
client.sync_clock()
c=DemoCoordinator(store,lease,client)
(cstore,c,fake),controls=configure((store,c,fake))
source=c.client
c.client=ObservationClient(source.settings,source.credentials,send=source.send,clock_ms=source.clock_ms)
c.client.sync_clock()
@asynccontextmanager
async def connector(url):
 async with connect(sys.argv[5]) as socket:
  yield socket
asyncio.run(worker(c,UUID(sys.argv[4]),connector=connector))
""")
    processes = []

    connections = 0

    async def handler(socket):
        nonlocal connections
        connections += 1
        if reconnect_close and connections > 1:
            await socket.close(code=1011)
        else:
            await socket.wait_closed()

    async with serve(handler, "127.0.0.1", 0) as server:
        url = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"

        @asynccontextmanager
        async def connector(url_secret):
            async with connect(url) as socket:
                yield socket

        class Process:
            def __init__(self, owner):
                env = os.environ.copy()
                env["PYTHONPATH"] = os.getcwd()
                self.process = subprocess.Popen(
                    [
                        sys.executable,
                        str(child_path),
                        store.engine.url.render_as_string(hide_password=False),
                        schema,
                        str(owner),
                        str(operation),
                        url,
                    ],
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
                self.pid = self.process.pid
                processes.append(self)

            def poll(self):
                return self.process.poll()

            def kill(self):
                self.process.kill()

            def wait(self, timeout=None):
                result = self.process.wait(timeout=timeout)
                if advance:
                    fake.at += timedelta(seconds=61)
                if patch:
                    patch()
                return result

        try:
            result = await module.run(
                store,
                client,
                operation,
                Process,
                account="DEMO-test",
                startup_timeout=10,
                lease_timeout=lease_timeout,
                poll_seconds=0.02,
                connector=connector,
            )
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.process.wait(timeout=5)
                error = process.process.stderr.read().decode()
                process.process.stderr.close()
                assert not error, error
        return result, operation


def test_empty_observer_real_sigkill_successor_fence_and_full_reconcile(case, tmp_path):  # noqa: F811
    store, _, fake = case
    before = store.read("DEMO-test")
    report, operation = asyncio.run(exercise(case, tmp_path))
    assert report["worker_recovery"] == "EMPTY_WORKER_SIGKILL_FENCED_RECOVERY_VERIFIED", json.dumps(
        report
    )
    assert report["forced_kill_verified"] and report["worker_exit_code"] == -9
    assert report["competing_leader_blocked"] and report["stale_owner_effect_blocked"]
    assert report["entry_gate_blocked_before_reconciliation"] and report["new_leader_reconciled"]
    assert report["lease_fence_after"] == report["lease_fence_before"] + 1
    assert report["failure"] == report["cleanup_failure"] == "NONE"
    assert report["history_and_baselines_preserved"] and report["leader_released"]
    after = store.read("DEMO-test")
    assert after.orders == before.orders and after.requests == before.requests
    assert after.paused and not after.stream_ready and not after.reconciled
    assert [(m, p) for m, p, _ in fake.calls if m != "GET"] == [
        ("POST", "/fapi/v1/listenKey"),
        ("DELETE", "/fapi/v1/listenKey"),
    ]
    assert after.operations[str(operation)] == "EMPTY_WORKER_READY"


def test_natural_expiry_is_required_and_no_forced_lease_reset(case, tmp_path):  # noqa: F811
    store, _, _ = case
    report, _ = asyncio.run(exercise(case, tmp_path, advance=False, lease_timeout=0.03))
    assert report["worker_recovery"] == "NOT_ACCEPTED"
    assert report["failure"] == "LEASE_WAIT_TIMEOUT"
    assert report["lease_fence_after"] is None and not report["leader_released"]
    assert store.read("DEMO-test").paused


def test_stale_effect_must_be_rejected_before_callback(case, tmp_path, monkeypatch):  # noqa: F811
    store, _, _ = case
    original = store.change

    def bypass(lease, marker, apply):
        if marker == "DEMO_EMPTY_WORKER_STALE_PROBE":
            return apply(store.read("DEMO-test"))
        return original(lease, marker, apply)

    monkeypatch.setattr(store, "change", bypass)
    report, _ = asyncio.run(exercise(case, tmp_path))
    assert report["worker_recovery"] == "NOT_ACCEPTED"
    assert report["failure"] == "STALE_OWNER_NOT_REJECTED"
    assert report["leader_released"] and report["new_entry_post_count"] == 0


def test_gate_cannot_be_skipped_before_reconciliation(case, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(module, "gate_blocked", lambda *args: False)
    report, _ = asyncio.run(exercise(case, tmp_path))
    assert report["failure"] == "RECOVERY_GATE_NOT_LOCKED"
    assert not report["new_leader_reconciled"] and report["leader_released"]


def test_recovery_wss_failure_is_not_hidden_by_final_pause(case, tmp_path):  # noqa: F811
    report, _ = asyncio.run(exercise(case, tmp_path, reconnect_close=True))
    assert report["worker_recovery"] == "NOT_ACCEPTED"
    assert report["failure"] != "NONE" and not report["new_leader_reconciled"]
    assert report["leader_released"] and report["final_paused"]


def test_unexpected_cash_flow_prevents_history_preserved_acceptance(case, tmp_path):  # noqa: F811
    _, _, fake = case

    def drift():
        fake.wallet += Decimal("0.1")
        fake.incomes.append(dict(incomeType="TRANSFER", asset="USDT", tranId=123, income="0.1"))

    report, _ = asyncio.run(exercise(case, tmp_path, patch=drift))
    assert report["worker_recovery"] == "NOT_ACCEPTED"
    assert not report["history_and_baselines_preserved"] and report["leader_released"]


def test_existing_operation_is_rejected_before_spawn(case):  # noqa: F811
    store, client, _ = case
    operation = uuid4()
    lease = store.acquire("DEMO-test", uuid4())
    store.change(
        lease,
        "TEST_OLD_OPERATION",
        lambda ledger: ledger.operations.update({str(operation): "EMPTY_WORKER_READY"}),
    )
    store.release(lease)
    with pytest.raises(module.RecoveryCheckBlocked) as caught:
        asyncio.run(
            module.run(
                store,
                client,
                operation,
                lambda owner: pytest.fail("must not spawn"),
                account="DEMO-test",
            )
        )
    assert caught.value.code == "OPERATION_ALREADY_EXISTS"


def test_exchange_position_is_rejected_before_spawn(case):  # noqa: F811
    store, client, fake = case
    fake.positions["BTCUSDT"] = Decimal("1")
    fake.prices["BTCUSDT"] = Decimal("100")
    with pytest.raises(module.RecoveryCheckBlocked) as caught:
        asyncio.run(
            module.run(
                store,
                client,
                uuid4(),
                lambda owner: pytest.fail("must not spawn"),
                account="DEMO-test",
            )
        )
    assert caught.value.code == "EMPTY_NORMAL_PAUSED_REQUIRED"


def test_failed_release_never_claims_verified_recovery(case, tmp_path, monkeypatch):  # noqa: F811
    store, _, _ = case

    def fail(lease):
        raise OSError("PRIVATE_DATABASE_URL")

    monkeypatch.setattr(store, "release", fail)
    report, _ = asyncio.run(exercise(case, tmp_path))
    assert report["worker_recovery"] == "NOT_ACCEPTED"
    assert report["cleanup_failure"] != "NONE" and not report["leader_released"]
    assert "PRIVATE" not in json.dumps(report)
