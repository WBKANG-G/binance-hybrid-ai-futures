"""All operational callers bind the installed parser; no per-caller monkeypatch."""

import importlib.util
import json
from pathlib import Path

import pytest

from integrations.binance_usdm import demo_snapshot
from scripts import check_demo_disconnect
from services.execution import (
    demo,
    demo_api_restart_check,
    demo_empty_db_restart,
    demo_empty_worker_recovery,
    demo_preview,
)
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_readiness_closed_history_cli import installed  # noqa: F401
from tests.unit.test_readiness_closed_history import INCIDENT, basis  # noqa: F401


def test_every_operational_consumer_uses_same_installed_function():
    assert hasattr(demo_snapshot, "receipt_proven_closed")
    assert (
        demo.snapshot
        is demo_preview.snapshot
        is demo_empty_db_restart.operational_snapshot
        is demo_empty_worker_recovery.operational_snapshot
        is demo_api_restart_check.operational_snapshot
        is check_demo_disconnect.operational_snapshot
        is demo_snapshot.operational_snapshot
    )
    assert demo.historical_snapshot is demo_snapshot.snapshot


def test_generic_operational_pair_and_readiness_share_financial_fields_and_fingerprint(installed):  # noqa: F811
    store, client, _ = installed
    ledger = store.read("DEMO-ready")
    before = ledger.model_dump_json()
    first = demo.snapshot(client, ledger, store.now())
    second = demo_preview.snapshot(client, ledger, store.now())
    assert first.model_dump(exclude={"as_of"}) == second.model_dump(exclude={"as_of"})
    assert ledger.orders[INCIDENT].client_id not in first.orders
    assert ledger.model_dump_json() == before


def test_full_history_remains_independent_and_includes_closed_original_ids(installed):  # noqa: F811
    store, client, fake = installed
    ledger = store.read("DEMO-ready")
    start = len(fake.calls)
    result = demo.historical_snapshot(client, ledger, store.now())
    assert ledger.orders[INCIDENT].client_id in result.orders
    assert sum(p.get("origClientOrderId") == "DE-test" for _, _, p in fake.calls[start:]) == 1
    assert sum(p.get("clientAlgoId") == "DS-test" for _, _, p in fake.calls[start:]) == 1


def test_previously_published_install_guard_rejects_unapproved_parser_before_keys_or_db(
    monkeypatch, capsys
):
    spec = importlib.util.spec_from_file_location(
        "old_approved_guard", Path("guard_readiness_history_install.py")
    )
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    monkeypatch.setattr("sys.argv", ["guard", "--phase", "post", "--expected-digest", "0" * 64])
    monkeypatch.setattr(
        guard, "credentials", lambda **k: pytest.fail("no keys for unapproved source")
    )
    monkeypatch.setattr(
        guard, "create_engine", lambda *a, **k: pytest.fail("no DB for unapproved source")
    )
    with pytest.raises(SystemExit) as error:
        guard.main()
    assert error.value.code == 2
    assert json.loads(capsys.readouterr().out)["stop_reason"] == "INSTALLED_SOURCE_HASH_MISMATCH"


def test_approved_reader_fixture_is_byte_identical_to_pre_extension_parser():
    import hashlib

    assert (
        hashlib.sha256(Path("tests/fixtures/approved_snapshot_v1.py").read_bytes()).hexdigest()
        == "02e5d789280f3ed8aa2667319248982d5cd4eedd2fb8b72e70aef8785e57ed6a"
    )


def test_worker_and_api_empty_checks_use_new_global_reader_without_db_changes(installed):  # noqa: F811
    from types import SimpleNamespace

    store, client, fake = installed
    ledger = store.read("DEMO-ready")
    original = ledger.model_dump_json()
    start = len(fake.calls)
    demo_empty_worker_recovery.remote_empty(store, client, "DEMO-ready")
    c = SimpleNamespace(store=store, client=client, lease=SimpleNamespace(account_id="DEMO-ready"))
    demo_api_restart_check.empty(c)
    from scripts.observe_demo_private import ObservationClient

    observer = ObservationClient(
        client.settings, client.credentials, send=client.send, clock_ms=client.clock_ms
    )
    disconnected = SimpleNamespace(
        store=store, client=observer, lease=SimpleNamespace(account_id="DEMO-ready")
    )
    check_demo_disconnect.remote_empty(disconnected)
    assert not any(p.get("origClientOrderId") == "DE-test" for _, _, p in fake.calls[start:])
    assert sum(p.get("origClientOrderId") == "DE-unadopted" for _, _, p in fake.calls[start:]) == 3
    assert store.read("DEMO-ready").model_dump_json() == original


def test_db_restart_digest_keeps_snapshot_pair_and22_signed_reads(installed):  # noqa: F811
    store, client, _ = installed
    before = store.read("DEMO-ready")
    digest, metrics = demo_empty_db_restart.exchange_digest(store, client, before)
    assert len(digest) == 64 and metrics["signed_get_count"] == 22
    assert store.read("DEMO-ready") == before


def test_legacy_fingerprint_is_blocked_until_fresh_reconciliation(installed):  # noqa: F811
    from tests.fixtures import approved_snapshot_v1

    store, client, fake = installed
    ledger = store.read("DEMO-ready")
    ledger.reconciled = True
    ledger.fingerprint = approved_snapshot_v1.operational_snapshot(
        client, ledger, store.now()
    ).fingerprint
    before = ledger.model_dump_json()
    with pytest.raises(demo_preview.PreviewBlocked, match="changed after reconciliation"):
        demo_preview.preview(client, ledger)
    assert ledger.model_dump_json() == before


def test_older_pool_install_guard_also_rejects_parser_before_callbacks():
    spec = importlib.util.spec_from_file_location(
        "old_pool_guard", Path("guard_tp_read_pool_install.py")
    )
    g = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g)
    with pytest.raises(g.GuardBlocked, match="SOURCE_HASH_MISMATCH"):
        g.run(
            None,
            "pre",
            digest=g.NEW_SHA,
            read_local=lambda *a: pytest.fail("no DB callback"),
            read_review=lambda: pytest.fail("no remote callback"),
        )
