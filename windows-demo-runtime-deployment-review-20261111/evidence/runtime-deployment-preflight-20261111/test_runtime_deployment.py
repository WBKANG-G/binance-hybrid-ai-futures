"""Local deployment review against an applied synthetic ledger and real PostgreSQL."""

# ruff: noqa: F811
import importlib.util
import json
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import event, select

from packages.persistence.demo import accounts, events, outbox

BASE = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "day_apply_fixture",
    BASE / "manual-close-day-apply-20261111/test_manual_close_day_apply_20261111.py",
)
prior = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prior)
setup = prior.setup
closed = prior.closed
demo_system = prior.demo_system
spec = importlib.util.spec_from_file_location(
    "deployment_review", Path(__file__).with_name("review_runtime_deployment.py")
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def snapshot(store):
    with store.engine.connect() as c:
        return [
            c.execute(select(t).order_by(*t.primary_key.columns)).all()
            for t in (accounts, events, outbox)
        ]


@pytest.fixture
def applied(setup, monkeypatch):
    store, *_ = setup
    prior.apply(setup)
    monkeypatch.setattr(m, "ACCOUNT", "DEMO-test")
    return setup


def test_current_core_sources_match_and_critical_files_are_pinned():
    assert len(m.INSTALLED_PINS) == 55
    assert m.source_cohort_valid(m.INSTALLED_PINS)
    inventory = m.candidate_inventory(Path("."))
    assert (
        len(inventory["matched"]) == 11 and not inventory["missing"] and not inventory["different"]
    )


def test_real_postgres_review_is_read_only_and_does_not_open_network_or_storage(
    applied, tmp_path, monkeypatch
):
    store, _, _, _, calls, *_ = applied
    before = snapshot(store)
    count = len(calls)
    for method in ("create", "acquire", "change", "release", "renew"):
        monkeypatch.setattr(store, method, lambda *a, **k: pytest.fail("store mutation forbidden"))
    seen = []

    def inspect(connection, cursor, statement, parameters, context, many):
        if statement.startswith("SELECT demo_accounts."):
            with connection.connection.cursor() as raw:
                raw.execute("SHOW transaction_read_only")
                ro = raw.fetchone()[0]
                raw.execute("SHOW transaction_isolation")
                seen.append((ro, raw.fetchone()[0]))

    event.listen(store.engine, "before_cursor_execute", inspect)
    try:
        report = m.review(store.engine, root=tmp_path / "missing", now=store.now())
    finally:
        event.remove(store.engine, "before_cursor_execute", inspect)
    assert seen == [("on", "repeatable read")]
    assert report["local_review_completed"] and report["period_keys_match"]
    assert report["durable_order_storage"] == "NOT_PROVISIONED"
    assert report["shared_weight_storage"] == "NOT_PROVISIONED"
    assert report["next_action"] == "PREPARE_CODE_ONLY_INSTALL_REVIEW"
    assert len(report["candidate_control_files"]["missing"]) == 11
    assert report["order_writes"] == report["http_attempts"] == 0
    assert report["execution_approved"] is False and report["credentials_accessed"] is False
    assert snapshot(store) == before and len(calls) == count
    assert not (tmp_path / "missing").exists()


def test_candidate_code_present_does_not_imply_storage_or_execution_ready(applied):
    store, *_ = applied
    report = m.review(store.engine, now=store.now())
    assert len(report["candidate_control_files"]["matched"]) == 11
    assert report["next_action"] == "PREPARE_STORAGE_CONFIGURATION_REVIEW"
    assert not report["execution_approved"] and not report["storage_files_verified"]
    assert "DURABLE_ORDER_STORAGE_NOT_PROVISIONED" in report["deployment_blockers"]


@pytest.mark.parametrize(
    "value", ["null", "[]", "{}", '{"journal":null}', '{"journal":"PRIVATE_SECRET"}']
)
def test_invalid_bindings_are_redacted(value):
    result = m.binding_status({"key": value}, "key")
    assert result == "BINDING_INVALID" and "PRIVATE_SECRET" not in result


def test_binding_presence_does_not_claim_files_valid():
    value = json.dumps({"journal": str(uuid4())})
    assert m.binding_status({"key": value}, "key") == "BINDING_PRESENT_FILES_NOT_CHECKED"
    shared = json.dumps(
        {
            "version": 1,
            "journal": str(uuid4()),
            "scope": "DEMO-scope",
            "checkpoint_installation": str(uuid4()),
            "policy": "unknown",
        }
    )
    assert (
        m.binding_status({"key": shared}, "key", shared=True) == "BINDING_PRESENT_FILES_NOT_CHECKED"
    )


@pytest.mark.parametrize(
    "field,value", [("version", True), ("scope", "LIVE-scope"), ("checkpoint_installation", "bad")]
)
def test_shared_binding_shape_is_checked(field, value):
    raw = {
        "version": 1,
        "journal": str(uuid4()),
        "scope": "DEMO-scope",
        "checkpoint_installation": str(uuid4()),
        "policy": "unknown",
    }
    raw[field] = value
    assert m.binding_status({"key": json.dumps(raw)}, "key", shared=True) == "BINDING_INVALID"


def test_candidate_mismatch_and_symlink_are_not_imported(tmp_path):
    target = tmp_path / "target"
    target.write_text('raise RuntimeError("must never import")')
    name = next(iter(m.CANDIDATE_PINS))
    path = tmp_path / name
    path.parent.mkdir(parents=True)
    path.symlink_to(target)
    result = m.candidate_inventory(tmp_path)
    assert result["different"] == [name]
    path.unlink()
    path.write_text("wrong version")
    assert m.candidate_inventory(tmp_path)["different"] == [name]


def test_active_leader_cannot_be_called_ready(applied):
    store, *_ = applied
    lease = store.acquire("DEMO-test", uuid4())
    before = snapshot(store)
    try:
        with pytest.raises(m.Blocked, match="ACTIVE_OWNER_BLOCKED"):
            m.review(store.engine, now=store.now())
        assert snapshot(store) == before
    finally:
        store.release(lease)


def test_day_rollover_is_reported_without_reset(applied):
    from datetime import timedelta

    store, *_ = applied
    before = snapshot(store)
    with pytest.raises(m.Blocked, match="PERIOD_KEYS_MISMATCH"):
        m.review(store.engine, now=store.now() + timedelta(days=1))
    assert snapshot(store) == before


def test_changed_day_receipt_rejected(applied):
    store, *_ = applied
    ledger = store.read("DEMO-test").model_copy(deep=True)
    ledger.operations[m.DAY_KEY] = "{}"
    with pytest.raises(m.Blocked, match="DAY_OPERATION_CONFLICT"):
        m.assess_row(
            {"state": ledger.model_dump(mode="json"), "owner": None, "lease_until": None},
            now=store.now(),
            root=Path("."),
        )


def test_no_arguments_or_apply_switch_can_open_database(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["review", "--apply"])
    monkeypatch.setattr(m, "create_engine", lambda *a, **kw: pytest.fail("DB opened"))
    with pytest.raises(SystemExit) as e:
        m.main()
    assert (
        e.value.code == 2
        and json.loads(capsys.readouterr().out)["stop_reason"] == "NO_ARGUMENTS_ACCEPTED"
    )


def test_source_mismatch_precedes_database(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["review"])
    monkeypatch.setattr(m, "source_cohort_valid", lambda *a: False)
    monkeypatch.setattr(m, "create_engine", lambda *a, **kw: pytest.fail("DB opened"))
    with pytest.raises(SystemExit) as e:
        m.main()
    assert (
        e.value.code == 2
        and json.loads(capsys.readouterr().out)["stop_reason"] == "INSTALLED_SOURCE_HASH_MISMATCH"
    )


def test_main_success_disposes_connection_and_exits_zero_for_completed_review(
    applied, monkeypatch, capsys
):
    store, *_ = applied

    class Engine:
        disposed = False

        def dispose(self):
            self.disposed = True

    engine = Engine()
    monkeypatch.setattr(sys, "argv", ["review"])
    monkeypatch.setattr(m, "create_engine", lambda *a, **kw: engine)
    real_review = m.review
    monkeypatch.setattr(m, "review", lambda e: real_review(store.engine, now=store.now()))
    before = snapshot(store)
    with pytest.raises(SystemExit) as e:
        m.main()
    report = json.loads(capsys.readouterr().out)
    assert e.value.code == 0 and engine.disposed and report["local_review_completed"]
    assert not report["execution_approved"] and snapshot(store) == before


def test_db_errors_are_redacted(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["review"])

    def fail(*a, **kw):
        raise OSError("PRIVATE_DB_URL")

    monkeypatch.setattr(m, "create_engine", fail)
    with pytest.raises(SystemExit) as e:
        m.main()
    out = capsys.readouterr().out
    assert e.value.code == 2 and "PRIVATE_DB_URL" not in out
    assert json.loads(out)["stop_reason"] == "LOCAL_REVIEW_FAILED"


def test_missing_source_stdlib_gate_precedes_sdk_import(tmp_path):
    import subprocess

    result = subprocess.run(
        [sys.executable, "-S", str(Path(m.__file__).resolve())],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2 and not result.stderr
    assert json.loads(result.stdout)["stop_reason"] == "INSTALLED_SOURCE_HASH_MISMATCH"
