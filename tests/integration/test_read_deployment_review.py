"""Candidate review validates prior receipt identity and never applies migration."""

# ruff: noqa: F811
import hashlib
import json

import pytest
from sqlalchemy import inspect, select, update

from packages.persistence.demo import accounts
from packages.persistence.demo_order_quota import LocalQuotaStopped
from scripts import review_demo_read_deployment as deployment
from services.execution.demo_runtime_storage import RuntimeClocks
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_read_audit_bootstrap import ready, state  # noqa: F401
from tests.integration.test_shared_weight_checkpoint import anchored  # noqa: F401


@pytest.fixture
def candidate(ready, tmp_path, monkeypatch):
    a = ready
    monkeypatch.setattr(deployment, "ACCOUNT", "DEMO-test")
    monkeypatch.setattr(deployment, "SCOPE", a[0].scope)
    monkeypatch.setattr(deployment, "STORAGE", a[3])
    monkeypatch.setattr(deployment, "create_engine", lambda *args, **kwargs: a[1].engine)
    with a[1].engine.begin() as c:
        raw = c.execute(select(accounts.c.state)).scalar_one()
        raw["operations"][deployment.SETUP_KEY] = json.dumps(
            dict(
                version=1,
                account="DEMO-test",
                source=deployment.SOURCE,
                scope=a[0].scope,
                directory="demo-runtime-20261111",
                shared_journal=str(a[4]),
            )
        )
        c.execute(update(accounts).values(state=raw))
    root = tmp_path / "candidate"
    installed = tmp_path / "installed"
    root.mkdir()
    installed.mkdir()
    for folder in (root, installed):
        (folder / "example.py").write_text("# pinned\n")
    digest = hashlib.sha256((root / "example.py").read_bytes()).hexdigest()
    (root / "CANDIDATE_MANIFEST.json").write_text(
        json.dumps(
            dict(
                baseline_reference=deployment.SOURCE,
                candidate_reference="a" * 40,
                installed_files={"example.py": digest},
                candidate_files={"example.py": digest},
            )
        )
    )
    return a, root, installed


def run(case):
    a, root, installed = case
    return deployment.review(
        root,
        installed_root=installed,
        clocks=RuntimeClocks(a[2].mono, a[2].utc, lambda: "test-boot"),
    )


def test_success_reports_plan_without_any_apply_or_installed_source_change(candidate):
    a, root, installed = candidate
    before = state(a)
    tables = inspect(a[1].engine).get_table_names()
    result = run(candidate)
    assert result["read_deployment_review"] == "CANDIDATE_PLAN_VERIFIED_KEEP_PAUSED"
    assert result["audit_schema_required"] and not result["audit_schema_created"]
    assert not result["api_replaced"] and not result["audit_apply_attempted"]
    assert not result["execution_approved"] and not result["orders_enabled"]
    assert state(a) == before and inspect(a[1].engine).get_table_names() == tables
    assert (installed / "example.py").read_text() == "# pinned\n"


@pytest.mark.parametrize("target", ["candidate", "installed"])
def test_source_mismatch_precedes_database_access(candidate, monkeypatch, target):
    _, root, installed = candidate
    ((root if target == "candidate" else installed) / "example.py").write_text("# altered\n")
    monkeypatch.setattr(deployment, "create_engine", lambda *a, **kw: pytest.fail("DB opened"))
    with pytest.raises(LocalQuotaStopped, match="SOURCE_HASH_MISMATCH"):
        run(candidate)


@pytest.mark.parametrize(
    "key,value",
    [
        ("source", "unknown"),
        ("directory", "other"),
        ("shared_journal", "11111111-1111-4111-8111-111111111111"),
        ("version", True),
    ],
)
def test_prior_receipt_cannot_be_replaced_by_discovered_journal(candidate, key, value):
    a, _, _ = candidate
    with a[1].engine.begin() as c:
        raw = c.execute(select(accounts.c.state)).scalar_one()
        record = json.loads(raw["operations"][deployment.SETUP_KEY])
        record[key] = value
        raw["operations"][deployment.SETUP_KEY] = json.dumps(record)
        c.execute(update(accounts).values(state=raw))
    before = state(a)
    with pytest.raises(LocalQuotaStopped, match="STORAGE_RECEIPTS_MISMATCH"):
        run(candidate)
    assert state(a) == before


@pytest.mark.parametrize("name", ["../escape.py", "/absolute.py"])
def test_manifest_cannot_reach_outside_root(tmp_path, name):
    with pytest.raises(LocalQuotaStopped, match="MANIFEST_INVALID"):
        deployment.verify_files(tmp_path, {name: "a" * 64})


def test_main_redacts_unexpected_failure(monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise RuntimeError("PRIVATE_DATABASE_URL")

    monkeypatch.setattr(deployment, "review", fail)
    assert deployment.main() == 1
    report = json.loads(capsys.readouterr().out)
    assert report["stop_reason"] == "DEPLOYMENT_REVIEW_UNAVAILABLE"
    assert "PRIVATE" not in str(report)


def test_explicit_recovery_bundle_waits_then_reaches_plan_without_audit_apply(candidate):
    from dataclasses import replace

    a, root, installed = candidate
    manifest = json.loads((root / "CANDIDATE_MANIFEST.json").read_text())
    manifest["operation"] = "recover-unused-clock"
    (root / "CANDIDATE_MANIFEST.json").write_text(json.dumps(manifest))
    with a[1].engine.begin() as c:
        raw = c.execute(select(accounts.c.state)).scalar_one()
        receipt = json.loads(raw["operations"][deployment.SETUP_KEY])
        from services.execution.demo_runtime_storage import RUNTIME_STORAGE_KEY

        binding = raw["operations"][RUNTIME_STORAGE_KEY]
        receipt.update(
            stage="PREPARED",
            anchor_installation=str(a[1].installation_id()),
            expected_runtime_binding=binding,
        )
        raw["operations"][deployment.SETUP_KEY] = json.dumps(receipt)
        c.execute(update(accounts).values(state=raw))
    current = replace(
        RuntimeClocks(a[2].mono, a[2].utc, lambda: "test-boot"), boot=lambda: "new-boot"
    )
    first = deployment.review(root, installed_root=installed, clocks=current)
    assert (
        first["read_deployment_review"] == "WAIT_STARTUP_HOLD" and first["shared_journal_changed"]
    )
    a[2].seconds += 70.002
    final = deployment.review(root, installed_root=installed, clocks=current)
    assert final["read_deployment_review"] == "CANDIDATE_PLAN_VERIFIED_KEEP_PAUSED"
    assert not final["shared_journal_changed"] and not final["audit_apply_attempted"]
    assert final["audit_binding_required"] and not final["audit_binding_created"]
