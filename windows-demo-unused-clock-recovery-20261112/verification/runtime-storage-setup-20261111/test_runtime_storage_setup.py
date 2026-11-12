"""Explicit storage setup with real PostgreSQL/SQLite, no exchange transport."""

# ruff: noqa: F811
import hashlib
import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text

from packages.persistence.demo import accounts, events, outbox
from services.execution.demo_runtime_storage import RuntimeClocks

BASE = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "apply_fixture",
    BASE / "manual-close-day-apply-20261111/test_manual_close_day_apply_20261111.py",
)
prior = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prior)
setup = prior.setup
closed = prior.closed
demo_system = prior.demo_system
spec = importlib.util.spec_from_file_location(
    "storage_setup", Path(__file__).with_name("configure_runtime_storage.py")
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def disk(root):
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


def rows(store):
    with store.engine.connect() as c:
        return [
            c.execute(select(t).order_by(*t.primary_key.columns)).all()
            for t in (accounts, events, outbox)
        ]


@pytest.fixture
def applied(setup, tmp_path, monkeypatch):
    from packages.persistence.demo_shared_checkpoint import SharedWeightAnchorStore

    prior.apply(setup)
    store, *_ = setup
    monkeypatch.setattr(m, "ACCOUNT", "DEMO-test")
    with store.engine.connect() as c:
        schema = c.execute(text("SELECT current_schema()")).scalar_one()
    engine = create_engine(
        store.engine.url, connect_args={"options": "-csearch_path=" + schema}, pool_size=3
    )
    anchor = SharedWeightAnchorStore(engine)
    parent = tmp_path / "export"
    parent.mkdir(mode=0o700)
    mono = [1000.0]

    def advance(seconds):
        mono[0] += seconds
        setup[6](seconds)

    clocks = RuntimeClocks(
        monotonic=lambda: mono[0],
        wall_clock=store.now,
        boot=lambda: "00000000-0000-0000-0000-000000000001",
    )
    yield store, anchor, parent, clocks, advance, setup
    engine.dispose()


def prepare(a):
    store, anchor, parent, clocks, *_ = a
    return m.prepare_storage(store, anchor, parent=parent, clocks=clocks)


def status(a):
    store, anchor, parent, clocks, *_ = a
    return m.setup_status(store, anchor, parent=parent, clocks=clocks)


def finalize(a):
    store, anchor, parent, clocks, *_ = a
    return m.finalize_storage(store, anchor, parent=parent, clocks=clocks)


def test_status_fresh_is_read_only_and_does_not_create_directories_or_tables(applied):
    from sqlalchemy import inspect

    store, anchor, parent, _, _, setup = applied
    before = rows(store)
    files = disk(parent)
    calls = len(setup[4])
    report = status(applied)
    assert report["runtime_storage_setup"] == "PREPARATION_REQUIRED"
    assert rows(store) == before and disk(parent) == files and len(setup[4]) == calls
    assert not inspect(anchor.engine).has_table("demo_shared_weight_installation")


def test_prepare_wait_finalize_preserves_financial_state_and_existing_receipts(applied):
    store, anchor, parent, clocks, advance, setup = applied
    before = store.read("DEMO-test")
    report = prepare(applied)
    assert report["runtime_storage_setup"] == "WAIT_STARTUP_HOLD"
    assert report["database_changed"] and report["filesystem_changed"] and report["leader_released"]
    middle = store.read("DEMO-test")
    assert m.setup_preserved(before) == m.setup_preserved(middle)
    assert middle.value == before.value and middle.orders == before.orders
    assert middle.weekly_base == before.weekly_base and middle.high_water == before.high_water
    assert m.SHARED_KEY not in middle.operations and m.ORDER_KEY in middle.operations
    with pytest.raises(m.Blocked, match="SETUP_STARTUP_HOLD_REQUIRED"):
        finalize(applied)
    advance(70.002)
    assert status(applied)["runtime_storage_setup"] == "READY_TO_FINALIZE"
    report = finalize(applied)
    assert report["runtime_storage_setup"] == "CONFIGURED_KEEP_PAUSED" and report["leader_released"]
    after = store.read("DEMO-test")
    assert m.setup_preserved(after) == m.setup_preserved(before)
    assert after.operations[m.DAY_KEY] == before.operations[m.DAY_KEY]
    assert after.attested_day_baselines == before.attested_day_baselines
    assert after.paused and after.risk_state.value == "NORMAL"
    assert after.daily_key == "2026-11-11" and after.weekly_key == "2026-11-09"
    assert m.SHARED_KEY in after.operations and m.DONE_KEY in after.operations
    assert m.setup_context(store) == after and not report["execution_approved"]
    assert len(setup[4]) == 23  # Initial synthetic proof only; setup performs no exchange GET/POST.


def test_successful_duplicate_and_read_status_have_no_pg_or_disk_writes(applied, monkeypatch):
    store, _, parent, _, advance, *_ = applied
    prepare(applied)
    advance(70.002)
    finalize(applied)
    before = rows(store)
    files = disk(parent)
    monkeypatch.setattr(store, "acquire", lambda *a: pytest.fail("duplicate lease"))
    monkeypatch.setattr(store, "change", lambda *a: pytest.fail("duplicate write"))
    assert prepare(applied)["runtime_storage_setup"] == "CONFIGURED_KEEP_PAUSED"
    assert finalize(applied)["runtime_storage_setup"] == "CONFIGURED_KEEP_PAUSED"
    assert status(applied)["runtime_storage_setup"] == "CONFIGURED_KEEP_PAUSED"
    assert rows(store) == before and disk(parent) == files


def test_prepared_duplicate_does_not_restart_hold_or_recreate_files(applied, monkeypatch):
    store, _, parent, _, advance, *_ = applied
    prepare(applied)
    advance(20)
    before = rows(store)
    files = disk(parent)
    monkeypatch.setattr(store, "acquire", lambda *a: pytest.fail("duplicate lease"))
    assert prepare(applied)["runtime_storage_setup"] == "WAIT_STARTUP_HOLD"
    assert rows(store) == before and disk(parent) == files
    advance(50.002)
    assert status(applied)["runtime_storage_setup"] == "READY_TO_FINALIZE"


@pytest.mark.parametrize("fault", ["permissions", "symlink", "orphan"])
def test_parent_and_existing_unknown_directory_fail_before_writes(applied, fault, tmp_path):
    store, _, parent, *_ = applied
    if fault == "permissions":
        parent.chmod(0o755)
    elif fault == "symlink":
        other = tmp_path / "other"
        other.mkdir(mode=0o700)
        parent.rmdir()
        parent.symlink_to(other, target_is_directory=True)
    else:
        (parent / m.STORAGE_DIRECTORY).mkdir(mode=0o700)
    before = rows(store)
    with pytest.raises(m.Blocked):
        prepare(applied)
    assert rows(store) == before


def test_existing_auxiliary_schema_is_not_adopted_or_reset(applied):
    store, anchor, _, *_ = applied
    anchor.provision()
    installation = anchor.installation_id()
    before = rows(store)
    with pytest.raises(m.Blocked, match="SETUP_AUXILIARY_SCHEMA_ALREADY_PRESENT"):
        prepare(applied)
    assert rows(store) == before and anchor.installation_id() == installation


def test_same_pool_is_not_used_for_shared_checkpoint(applied):
    from packages.persistence.demo_shared_checkpoint import SharedWeightAnchorStore

    store, _, parent, clocks, *_ = applied
    before = rows(store)
    with pytest.raises(m.Blocked, match="SETUP_DEDICATED_POOL_REQUIRED"):
        m.prepare_storage(
            store, SharedWeightAnchorStore(store.engine), parent=parent, clocks=clocks
        )
    assert rows(store) == before


def test_active_owner_blocks_before_files_or_schema(applied):
    store, _, parent, *_ = applied
    lease = store.acquire("DEMO-test", uuid4())
    before = rows(store)
    files = disk(parent)
    try:
        with pytest.raises(m.Blocked, match="SETUP_ACTIVE_OWNER"):
            prepare(applied)
        assert rows(store) == before and disk(parent) == files
    finally:
        store.release(lease)


def test_date_rollover_blocks_without_resetting_baselines(applied):
    store, _, parent, _, advance, *_ = applied
    advance(86400)
    before = rows(store)
    with pytest.raises(m.Blocked, match="SETUP_PERIOD_MISMATCH"):
        prepare(applied)
    assert rows(store) == before and not (parent / m.STORAGE_DIRECTORY).exists()


@pytest.mark.parametrize("fault_at", ["mkdir", "order", "schema", "shared", "prepared_commit"])
def test_partial_failure_never_recreates_or_repairs_unknown_state(applied, monkeypatch, fault_at):
    from services.execution.demo_managed_order_quota import ManagedQuotaBridge

    store, anchor, parent, *_ = applied
    original = store.read("DEMO-test")
    progress = {}

    def fail(*a, **kw):
        raise OSError("SYNTHETIC_FAILURE")

    if fault_at == "mkdir":
        old = Path.mkdir

        def mkdir(path, *a, **kw):
            if path.name == m.STORAGE_DIRECTORY:
                raise OSError("SYNTHETIC_FAILURE")
            return old(path, *a, **kw)

        monkeypatch.setattr(Path, "mkdir", mkdir)
    elif fault_at == "order":
        monkeypatch.setattr(ManagedQuotaBridge, "bootstrap", fail)
    elif fault_at == "schema":
        monkeypatch.setattr(anchor, "provision", fail)
    elif fault_at == "shared":
        monkeypatch.setattr(anchor, "bootstrap", fail)
    else:
        change = store.change

        def changed(lease, kind, fn):
            if kind == "DEMO_RUNTIME_STORAGE_SETUP_PREPARED":
                raise OSError("SYNTHETIC_FAILURE")
            return change(lease, kind, fn)

        monkeypatch.setattr(store, "change", changed)
    with pytest.raises(OSError):
        m.prepare_storage(store, anchor, parent=parent, clocks=applied[3], progress=progress)
    after = store.read("DEMO-test")
    assert m.setup_preserved(after) == m.setup_preserved(original)
    assert after.paused and m.DONE_KEY not in after.operations
    assert m.setup_context(store) == after and progress["database_may_have_changed"]
    before = rows(store)
    files = disk(parent)
    with pytest.raises(m.Blocked, match="SETUP_INCOMPLETE_NO_AUTOMATIC_RETRY"):
        prepare(applied)
    assert rows(store) == before and disk(parent) == files


@pytest.mark.parametrize(
    "fault", ["intent", "day_receipt", "wallet", "weekly_base", "high_water", "done"]
)
def test_no_reinterpretation_of_changed_financial_state_or_receipts(applied, fault):
    store, _, _, _, advance, *_ = applied
    prepare(applied)
    advance(70.002)
    finalize(applied)
    lease = store.acquire("DEMO-test", uuid4())

    def damage(ledger):
        if fault == "intent":
            ledger.operations[m.SETUP_KEY] = "{}"
        elif fault == "day_receipt":
            ledger.operations[m.DAY_KEY] = "{}"
        elif fault == "wallet":
            ledger.value = ledger.value.model_copy(update={"realized": ledger.value.realized + 1})
        elif fault == "weekly_base":
            ledger.weekly_base += 1
        elif fault == "high_water":
            ledger.high_water += 1
        else:
            ledger.operations[m.DONE_KEY] = "{}"

    store.change(lease, "SYNTHETIC_CORRUPTION", damage)
    store.release(lease)
    before = rows(store)
    with pytest.raises(m.Blocked):
        status(applied)
    assert rows(store) == before


def test_incomplete_final_receipt_commit_can_be_finalized_without_rebinding_or_reset(
    applied, monkeypatch
):
    store, _, parent, _, advance, *_ = applied
    prepare(applied)
    advance(70.002)
    original = store.read("DEMO-test")
    change = store.change

    def changed(lease, kind, fn):
        if kind == "DEMO_RUNTIME_STORAGE_SETUP_COMPLETED":
            raise OSError("SYNTHETIC_INTERRUPTION")
        return change(lease, kind, fn)

    monkeypatch.setattr(store, "change", changed)
    with pytest.raises(OSError):
        finalize(applied)
    after = store.read("DEMO-test")
    assert m.SHARED_KEY in after.operations and m.DONE_KEY not in after.operations
    assert m.setup_preserved(after) == m.setup_preserved(original)
    files = disk(parent)
    monkeypatch.setattr(store, "change", change)
    assert finalize(applied)["runtime_storage_setup"] == "CONFIGURED_KEEP_PAUSED"
    assert disk(parent) == files


def test_prepared_root_cannot_be_replaced_by_symlink(applied, tmp_path):
    store, _, parent, *_ = applied
    prepare(applied)
    root = parent / m.STORAGE_DIRECTORY
    other = tmp_path / "moved"
    root.rename(other)
    root.symlink_to(other, target_is_directory=True)
    before = rows(store)
    with pytest.raises(m.Blocked, match="SETUP_PRIVATE_ROOT_REQUIRED"):
        status(applied)
    assert rows(store) == before


def test_order_session_is_not_treated_as_unused_even_without_flows(applied):
    from uuid import UUID

    from packages.persistence.demo_order_quota import DurableOrderQuota

    store, _, parent, clocks, *_ = applied
    prepare(applied)
    record = m._read_setup(store.read("DEMO-test"))
    q = DurableOrderQuota(
        parent / m.STORAGE_DIRECTORY / "order-quota",
        "DEMO-test",
        UUID(record["order_journal"]),
        monotonic=clocks.monotonic,
        wall_clock=clocks.wall_clock,
    )
    try:
        q.start_session(uuid4(), 99)
    finally:
        q.close()
    before = rows(store)
    with pytest.raises(m.Blocked, match="SETUP_ORDER_FLOWS_NOT_EMPTY"):
        status(applied)
    assert rows(store) == before


def test_expired_shared_charge_is_not_reinterpreted_as_unused_storage(applied):
    from packages.persistence.demo_shared_checkpoint import AnchoredSharedWeightJournal

    store, anchor, parent, clocks, advance, *_ = applied
    prepare(applied)
    advance(70.002)
    record = m._read_setup(store.read("DEMO-test"))
    storage = m._storage_objects(anchor, parent / m.STORAGE_DIRECTORY, record, clocks)
    journal = AnchoredSharedWeightJournal(
        storage.shared_directory,
        storage.shared_scope,
        storage.shared_identity,
        anchors=anchor,
        monotonic=clocks.monotonic,
        wall_clock=clocks.wall_clock,
        boot=clocks.boot,
    )
    token = journal.reserve(5, "RESCUE")
    journal.finish_unobserved(token, status=200)
    advance(70)
    before = rows(store)
    with pytest.raises(m.Blocked, match="SETUP_SHARED_REQUESTS_NOT_EMPTY"):
        status(applied)
    assert rows(store) == before


def test_cli_requires_apply_flag_before_any_database_access(monkeypatch, capsys):
    import sys

    monkeypatch.setattr(sys, "argv", ["setup", "--phase", "prepare"])
    monkeypatch.setattr(m, "create_engine", lambda *a, **kw: pytest.fail("DB opened"))
    with pytest.raises(SystemExit) as e:
        m.setup_main()
    report = json.loads(capsys.readouterr().out)
    assert e.value.code == 2 and report["stop_reason"] == "SETUP_EXPLICIT_APPLY_REQUIRED"
    assert not report["database_may_have_changed"]


def test_missing_candidate_cli_has_no_database_or_credential_access_in_old_docker():
    import os
    import subprocess

    helper = Path(m.__file__).resolve()
    helper.chmod(0o644)
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--user",
            "10001",
            "-e",
            "PYTHONPATH=/app",
            "--mount",
            f"type=bind,source={helper},target=/tmp/setup.py,readonly",
            "--entrypoint",
            "python",
            "phase4-tp-budget-install-smoke:20261109",
            "/tmp/setup.py",
            "--phase",
            "status",
        ],
        env=os.environ | {"DOCKER_CONFIG": "/tmp/tp-budget-build-docker"},
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert result.returncode == 4, result.stderr
    report = json.loads(result.stdout)
    assert report["runtime_storage_setup"] == "CODE_INSTALL_REQUIRED"
    assert (
        report["database_changed"] is False
        and report["http_attempts"] == 0
        and not report["credentials_accessed"]
    )


def test_real_docker_processes_share_persistent_storage_and_honor_natural_startup_hold(applied):
    import os
    import subprocess
    import time

    store, _, _, _, _, setup = applied
    env = os.environ | {"DOCKER_CONFIG": "/tmp/tp-budget-build-docker"}
    address = subprocess.check_output(
        [
            "docker",
            "inspect",
            "phase4-alert-pipeline-fresh-db",
            "--format",
            "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
        ],
        env=env,
        text=True,
    ).strip()
    with store.engine.connect() as c:
        schema = c.execute(text("SELECT current_schema()")).scalar_one()
    helper = Path(m.__file__).resolve()
    helper.chmod(0o644)
    original = store.read("DEMO-test")
    volume = "phase4-storage-setup-test-" + uuid4().hex
    subprocess.run(["docker", "volume", "create", volume], env=env, capture_output=True, check=True)
    base_mono = time.monotonic()
    base_wall = store.now().isoformat()
    program = """import importlib.util,json,sys,time
from datetime import datetime,timedelta
from sqlalchemy import create_engine
from packages.persistence.demo import DemoStore
from packages.persistence.demo_shared_checkpoint import SharedWeightAnchorStore
from services.execution.demo_runtime_storage import RuntimeClocks
s=importlib.util.spec_from_file_location('setup','/tmp/setup.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
assert m.source_cohort_valid(m.INSTALLED_PINS)
m.verify_install_files('post');m.verify_default_refusal();m.ACCOUNT='DEMO-test'
base=datetime.fromisoformat(sys.argv[3]);start=float(sys.argv[4])
now=lambda:base+timedelta(seconds=time.monotonic()-start)
a=create_engine(sys.argv[1],connect_args={'options':'-csearch_path='+sys.argv[2],'connect_timeout':2})
b=create_engine(sys.argv[1],connect_args={'options':'-csearch_path='+sys.argv[2],'connect_timeout':2})
try:
 store=DemoStore(a,clock=now);anchor=SharedWeightAnchorStore(b)
 action={'prepare':m.prepare_storage,'status':m.setup_status,'finalize':m.finalize_storage}[sys.argv[5]]
 try:r=action(store,anchor,clocks=RuntimeClocks(wall_clock=now))
 except m.Blocked as e:r={'runtime_storage_setup':'EXPECTED_BLOCKED','stop_reason':str(e)}
 print(json.dumps(r))
finally:b.dispose();a.dispose()
"""

    def run(phase):
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "bridge",
                "--user",
                "10001",
                "-e",
                "PYTHONPATH=/app",
                "--mount",
                f"type=bind,source={helper},target=/tmp/setup.py,readonly",
                "-v",
                volume + ":/app/demo-export-state",
                "--entrypoint",
                "python",
                "phase4-runtime-code-install:20261111",
                "-c",
                program,
                f"postgresql+psycopg://dev:dev@{address}:5432/hybrid",
                schema,
                base_wall,
                str(base_mono),
                phase,
            ],
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=25,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    try:
        assert run("prepare")["runtime_storage_setup"] == "WAIT_STARTUP_HOLD"
        assert run("finalize")["stop_reason"] == "SETUP_STARTUP_HOLD_REQUIRED"
        deadline = time.monotonic() + 115
        while True:
            report = run("status")
            if report["runtime_storage_setup"] == "READY_TO_FINALIZE":
                break
            assert report["runtime_storage_setup"] == "WAIT_STARTUP_HOLD"
            assert time.monotonic() < deadline
            time.sleep(3)
        assert time.monotonic() - base_mono >= 70.001
        assert run("finalize")["runtime_storage_setup"] == "CONFIGURED_KEEP_PAUSED"
        assert run("status")["runtime_storage_setup"] == "CONFIGURED_KEEP_PAUSED"
        after = store.read("DEMO-test")
        assert m.setup_preserved(after) == m.setup_preserved(original)
        assert not any(after.positions.values()) and after.paused
        assert len(setup[4]) == 23
    finally:
        subprocess.run(["docker", "volume", "rm", volume], env=env, capture_output=True, check=True)
