"""Code-only deployment guards, synthetic applied ledger and real PostgreSQL."""

# ruff: noqa: F811
import hashlib
import importlib.util
import json
import sys
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import event, select

from packages.persistence.demo import accounts, events, outbox

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
    "install_guard", Path(__file__).with_name("guard_runtime_code_install.py")
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


@pytest.fixture
def applied(setup, monkeypatch):
    prior.apply(setup)
    monkeypatch.setattr(m, "ACCOUNT", "DEMO-test")
    return setup


def rows(store):
    with store.engine.connect() as c:
        return [
            c.execute(select(t).order_by(*t.primary_key.columns)).all()
            for t in (accounts, events, outbox)
        ]


def test_pre_post_snapshots_preserve_accounts_events_outbox_and_baselines(applied):
    store, *_ = applied
    before = rows(store)
    pre = m.install_review(store.engine, "pre", now=store.now())
    post = m.install_review(
        store.engine, "post", expected=pre["state_snapshot_sha256"], now=store.now()
    )
    assert post["runtime_code_install"] == "POSTCHECKED_CODE_ONLY_KEEP_PAUSED"
    assert post["database_snapshot_unchanged"] and post["default_execution_disabled_verified"]
    assert post["dependency_files_verified"] == 66
    assert post["day_kst"] == "2026-11-11" and post["week_kst"] == "2026-11-09"
    assert post["durable_order_storage"] == post["shared_weight_storage"] == "NOT_PROVISIONED"
    assert post["execution_approved"] is False and post["http_attempts"] == 0
    assert rows(store) == before


def test_post_database_digest_mismatch_cannot_report_install_success(applied):
    store, *_ = applied
    before = rows(store)
    with pytest.raises(m.Blocked, match="INSTALL_DATABASE_STATE_CHANGED"):
        m.install_review(store.engine, "post", expected="0" * 64, now=store.now())
    assert rows(store) == before


@pytest.mark.parametrize("expected", [None, "bad", "0" * 63])
def test_post_requires_precheck_digest_before_database(applied, expected, monkeypatch):
    store, *_ = applied
    monkeypatch.setattr(m, "install_snapshot", lambda *a, **kw: pytest.fail("SQL forbidden"))
    with pytest.raises(m.Blocked, match="INSTALL_EXPECTED_SNAPSHOT_REQUIRED"):
        m.install_review(store.engine, "post", expected=expected, now=store.now())


def test_snapshot_transaction_is_repeatable_read_read_only(applied):
    store, *_ = applied
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
        m.install_snapshot(store.engine, now=store.now())
    finally:
        event.remove(store.engine, "before_cursor_execute", inspect)
    assert seen == [("on", "repeatable read")]


def test_active_owner_blocks_install(applied):
    store, *_ = applied
    lease = store.acquire("DEMO-test", uuid4())
    before = rows(store)
    try:
        with pytest.raises(m.Blocked, match="ACTIVE_OWNER_BLOCKED"):
            m.install_snapshot(store.engine, now=store.now())
        assert rows(store) == before
    finally:
        store.release(lease)


def test_day_rollover_blocks_install_without_baseline_reset(applied):
    store, *_ = applied
    before = rows(store)
    with pytest.raises(m.Blocked, match="PERIOD_KEYS_MISMATCH"):
        m.install_snapshot(store.engine, now=store.now() + timedelta(days=1))
    assert rows(store) == before


@pytest.fixture
def small_files(tmp_path, monkeypatch):
    payload = {"old.py": "same", "new.py": "new"}
    for name, data in payload.items():
        (tmp_path / name).write_text(data)
    target = {n: hashlib.sha256(v.encode()).hexdigest() for n, v in payload.items()}
    monkeypatch.setattr(m, "TARGET_PINS", target)
    monkeypatch.setattr(m, "BASELINE_PINS", {"old.py": target["old.py"], "new.py": None})
    return tmp_path


def test_new_modules_can_be_missing_only_before_install(small_files):
    (small_files / "new.py").unlink()
    m.verify_install_files("pre", small_files)
    with pytest.raises(m.Blocked, match="INSTALL_SOURCE_CONFLICT"):
        m.verify_install_files("post", small_files)


def test_existing_dependency_must_not_be_missing(small_files):
    (small_files / "old.py").unlink()
    with pytest.raises(m.Blocked, match="INSTALL_DEPENDENCY_MISSING"):
        m.verify_install_files("pre", small_files)


@pytest.mark.parametrize("phase", ["pre", "post"])
def test_unknown_file_version_is_not_overwritten(small_files, phase):
    (small_files / "old.py").write_text("external patch")
    with pytest.raises(m.Blocked, match="INSTALL_SOURCE_CONFLICT"):
        m.verify_install_files(phase, small_files)


def test_symlinks_are_not_accepted_as_install_targets(small_files):
    (small_files / "new.py").unlink()
    (small_files / "new.py").symlink_to(small_files / "old.py")
    with pytest.raises(m.Blocked, match="INSTALL_SOURCE_CONFLICT"):
        m.verify_install_files("pre", small_files)


def test_default_execution_refusal_occurs_before_any_db_or_storage():
    m.verify_default_refusal()


def test_default_refusal_removal_is_detected(monkeypatch):
    import services.execution.demo_managed_runtime as runtime

    monkeypatch.setattr(runtime, "assemble_managed_runtime", lambda *a: None)
    with pytest.raises(m.Blocked, match="DEFAULT_EXECUTION_REFUSAL_CHANGED"):
        m.verify_default_refusal()


def test_payload_is_additive_against_reviewed_baseline():
    reference = json.loads(Path(__file__).with_name("PAYLOAD_REFERENCE.json").read_text())
    old = reference["baseline_files"]
    target = reference["target_files"]
    assert len(target) == 66 and sum(v is None for v in old.values()) == 15
    assert all(old[n] is None or old[n] == h for n, h in target.items())
    assert m.source_cohort_valid(m.INSTALLED_PINS)
    assert all(hashlib.sha256(Path(n).read_bytes()).hexdigest() == h for n, h in target.items())


def test_install_cli_failure_redacts_connection_details(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["guard", "--phase", "pre"])

    def fail(*a, **kw):
        raise OSError("PRIVATE_DB_URL")

    monkeypatch.setattr(m, "create_engine", fail)
    with pytest.raises(SystemExit) as e:
        m.install_main()
    out = capsys.readouterr().out
    assert e.value.code == 2 and "PRIVATE_DB_URL" not in out
    assert json.loads(out)["stop_reason"] == "INSTALL_REVIEW_FAILED"


def test_live_context_is_rejected_before_db(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["guard", "--phase", "pre"])
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    monkeypatch.setattr(m, "create_engine", lambda *a, **kw: pytest.fail("DB opened"))
    with pytest.raises(SystemExit) as e:
        m.install_main()
    assert (
        e.value.code == 2
        and json.loads(capsys.readouterr().out)["stop_reason"] == "LIVE_CONTEXT_FORBIDDEN"
    )


def test_powershell_replaces_only_api_and_reuses_original_compose_metadata():
    script = Path(__file__).with_name("INSTALL_CODE.ps1").read_text()
    assert ". (Join-Path $root 'INSTALL_INSPECTION.ps1')" in script
    script += Path(__file__).with_name("INSTALL_INSPECTION.ps1").read_text()
    assert "'--no-deps','--no-build','--pull','never','--wait'" in script
    assert "'--wait-timeout','90','api'" in script
    assert "com.docker.compose.project.config_files" in script
    assert "com.docker.compose.project.working_dir" in script
    assert "'--expected-state',$pre.state_snapshot_sha256" in script
    assert "$finalPre.state_snapshot_sha256 -ne $pre.state_snapshot_sha256" in script
    assert "docker rm" not in script and "docker volume" not in script
    assert "allow_synthetic_model_execution" not in script
    assert "credentials_accessed = $false" in script
    assert all(
        name not in script for name in ("Get-Credential", "demo_key_session", "credentials(")
    )


def test_real_docker_base_and_installed_image_preserve_same_postgres_snapshot(applied):
    import os
    import subprocess

    store, *_ = applied
    before = rows(store)
    address = subprocess.check_output(
        [
            "docker",
            "inspect",
            "phase4-alert-pipeline-fresh-db",
            "--format",
            "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
        ],
        env=os.environ | {"DOCKER_CONFIG": "/tmp/tp-budget-build-docker"},
        text=True,
    ).strip()
    with store.engine.connect() as conn:
        schema = conn.exec_driver_sql("SELECT CURRENT_SCHEMA()").scalar_one()
    guard = str(Path(m.__file__).resolve())
    # Cloud-only synthetic fixture account; the published CLI never accepts an account override.
    program = """import importlib.util,json,sys
from datetime import datetime
from sqlalchemy import create_engine
s=importlib.util.spec_from_file_location('guard','/tmp/guard.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
assert m.source_cohort_valid(m.INSTALLED_PINS)
m.ACCOUNT='DEMO-test'
engine=create_engine(sys.argv[1],connect_args={'connect_timeout':2,'options':'-csearch_path='+sys.argv[5]})
try:r=m.install_review(engine,sys.argv[2],expected=sys.argv[3] or None,now=datetime.fromisoformat(sys.argv[4]))
finally:engine.dispose()
print(json.dumps(r))
"""

    def run(image, phase, expected=""):
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
                f"type=bind,source={guard},target=/tmp/guard.py,readonly",
                "--entrypoint",
                "python",
                image,
                "-c",
                program,
                f"postgresql+psycopg://dev:dev@{address}:5432/hybrid",
                phase,
                expected,
                store.now().isoformat(),
                schema,
            ],
            env=os.environ | {"DOCKER_CONFIG": "/tmp/tp-budget-build-docker"},
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    pre = run("phase4-tp-budget-install-smoke:20261109", "pre")
    post = run("phase4-runtime-code-install:20261111", "post", pre["state_snapshot_sha256"])
    assert post["database_snapshot_unchanged"] and post["candidate_code_installed"]
    assert post["default_execution_disabled_verified"] and not post["execution_approved"]
    assert rows(store) == before
