"""Image installation guard preserves all registered state and refuses partial source."""

# ruff: noqa: F811
import hashlib
import json
import shutil
from pathlib import Path

import pytest
from sqlalchemy import select, text, update

from packages.persistence.demo import accounts
from packages.persistence.demo_order_quota import LocalQuotaStopped
from scripts import review_demo_read_code_install as guard
from scripts import review_demo_read_deployment as deployment
from services.execution.demo_read_audit_bootstrap import prepare_read_audit
from services.execution.demo_runtime_storage import RuntimeClocks
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_read_audit_bootstrap import ready  # noqa: F401
from tests.integration.test_read_deployment_review import candidate  # noqa: F401
from tests.integration.test_shared_weight_checkpoint import anchored  # noqa: F401


@pytest.fixture
def installation(candidate, monkeypatch):
    a, root, installed = candidate
    parent = a[3] / "storage-only"
    parent.mkdir(mode=0o700)
    shared = parent / "shared-weight"
    shared.mkdir(mode=0o700)
    for path in (a[0].path, a[0].lock_path):
        shutil.move(path, shared / path.name)
    monkeypatch.setattr(deployment, "STORAGE", shared)
    monkeypatch.setattr(guard, "create_engine", lambda *args, **kw: a[1].engine)
    monkeypatch.setattr(
        guard,
        "installed_review",
        lambda *args: dict(installed_command_verified=True, audit_events=0),
    )
    current = RuntimeClocks(a[2].mono, a[2].utc, lambda: "test-boot")
    prepare_read_audit(
        a[1].engine,
        "DEMO-test",
        shared,
        a[0].scope,
        a[4],
        a[1].installation_id(),
        apply=True,
        readers_stopped=True,
        clocks=current,
    )
    record = json.loads((root / "CANDIDATE_MANIFEST.json").read_text())
    record["install_files"] = {
        name: hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in guard.INSTALL_FILES
    }
    (root / "CANDIDATE_MANIFEST.json").write_text(json.dumps(record))
    return a, root, installed, current


def run(case, phase="pre", expected=None):
    _, root, installed, clock = case
    return guard.review(root, installed=installed, phase=phase, expected=expected, clocks=clock)


def copy_target(case):
    for name in guard.INSTALL_FILES:
        path = case[2] / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(name, path)


def test_pre_and_post_preserve_all_database_and_storage_state(installation):
    before = run(installation)
    assert not before["target_already_installed"] and not before["installed_command_verified"]
    copy_target(installation)
    after = run(installation, "post", before["preserved_snapshot_sha256"])
    assert after["installed_command_verified"] and after["target_file_count"] == 6
    assert before["preserved_snapshot_sha256"] == after["preserved_snapshot_sha256"]
    assert run(installation)["target_already_installed"]


def test_partial_source_refuses_before_database_access(installation, monkeypatch):
    target = installation[2] / guard.INSTALL_FILES[0]
    target.parent.mkdir(parents=True)
    target.write_text("partial")
    monkeypatch.setattr(guard, "create_engine", lambda *a, **kw: pytest.fail("DB opened"))
    with pytest.raises(LocalQuotaStopped, match="PARTIAL_SOURCE"):
        run(installation)


def test_changed_installed_target_is_not_overwritten(installation):
    copy_target(installation)
    (installation[2] / guard.INSTALL_FILES[0]).write_text("changed")
    with pytest.raises(LocalQuotaStopped, match="SOURCE_HASH_MISMATCH"):
        run(installation)


def test_post_requires_expected_snapshot_and_all_source_files(installation):
    with pytest.raises(LocalQuotaStopped, match="EXPECTED_SNAPSHOT_REQUIRED"):
        run(installation, "post")
    with pytest.raises(LocalQuotaStopped, match="SOURCE_NOT_INSTALLED"):
        run(installation, "post", "a" * 64)


def test_financial_change_is_detected_before_installed_command(installation, monkeypatch):
    before = run(installation)
    copy_target(installation)
    a = installation[0]
    with a[1].engine.begin() as c:
        raw = c.execute(select(accounts.c.state)).scalar_one()
        raw["high_water"] = "2"
        c.execute(update(accounts).values(state=raw))
    monkeypatch.setattr(
        guard, "installed_review", lambda *args: pytest.fail("installed command started")
    )
    with pytest.raises(LocalQuotaStopped, match="PRESERVED_STATE_CHANGED"):
        run(installation, "post", before["preserved_snapshot_sha256"])


def test_audit_setup_is_required_and_not_created_by_install_guard(installation):
    a = installation[0]
    with a[1].engine.begin() as c:
        c.execute(text("DROP TABLE demo_read_audit_events"))
        c.execute(text("DROP TABLE demo_read_audit_bindings"))
    with pytest.raises(LocalQuotaStopped, match="AUDIT_SETUP_REQUIRED"):
        run(installation)


def test_storage_backup_change_affects_snapshot(installation):
    before = run(installation)
    path = deployment.STORAGE.parent / "new-backup"
    path.write_bytes(b"changed")
    path.chmod(0o600)
    with pytest.raises(LocalQuotaStopped, match="PRESERVED_STATE_CHANGED"):
        run(installation, "pre", before["preserved_snapshot_sha256"])


def test_main_redacts_unknown_error(monkeypatch, capsys):
    import sys

    monkeypatch.setattr(sys, "argv", ["-c", "a" * 64, "post", "b" * 64])

    def fail(*args, **kw):
        raise RuntimeError("PRIVATE_DATABASE_URL")

    monkeypatch.setattr(guard, "review", fail)
    assert guard.main() == 1
    report = json.loads(capsys.readouterr().out)
    assert report["stop_reason"] == "READ_INSTALL_REVIEW_UNAVAILABLE"
    assert "PRIVATE" not in str(report)


def test_empty_native_shell_argument_is_not_an_expected_snapshot(monkeypatch, capsys):
    import sys

    monkeypatch.setattr(sys, "argv", ["-c", "a" * 64, "pre", ""])

    def check(*args, phase, expected, **kwargs):
        assert phase == "pre" and expected is None
        return dict(read_code_install="PRECHECKED_READ_CODE_INSTALL_KEEP_PAUSED")

    monkeypatch.setattr(guard, "review", check)
    assert guard.main() == 0
    assert (
        json.loads(capsys.readouterr().out)["read_code_install"]
        == "PRECHECKED_READ_CODE_INSTALL_KEEP_PAUSED"
    )
