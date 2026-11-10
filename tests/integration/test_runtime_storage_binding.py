"""A valid empty store must not replace the account's configured pressure scope."""

# ruff: noqa: F811
import asyncio
from contextlib import contextmanager
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from packages.persistence.demo_order_quota import LocalQuotaStopped
from packages.persistence.demo_shared_checkpoint import SharedWeightAnchorStore, installations
from services.execution.demo_managed_runtime import RuntimeStorage
from services.execution.demo_runtime_storage import RUNTIME_STORAGE_KEY, bind_runtime_storage
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_managed_order_quota import managed  # noqa: F401
from tests.integration.test_managed_runtime_assembly import assembly, open_session  # noqa: F401
from tests.integration.test_quota_startup_review import snapshot
from tests.unit.test_quota_startup_read_only import files


def test_valid_empty_other_scope_cannot_replace_scope_with_pending_requests(assembly):
    store, c, q, clock, w, source, sender, calls, storage, clocks = assembly
    w.reserve(1000, "RESCUE")
    folder = storage.order_directory / "other-pressure"
    folder.mkdir(mode=0o700)
    identity = uuid4()
    storage.shared_anchor.bootstrap(
        folder, "DEMO-other", identity, monotonic=clock.mono, wall_clock=clock.utc, boot=clocks.boot
    )
    clock.seconds += 70.01
    alternate = RuntimeStorage(
        storage.order_directory, folder, "DEMO-other", identity, storage.shared_anchor
    )
    args = list(assembly)
    args[8] = alternate
    before, disk = snapshot(store), files(storage.order_directory)
    created = None
    try:
        with pytest.raises(LocalQuotaStopped, match="STORAGE_BINDING"):
            created = open_session(args, allow_synthetic_model_execution=True)
    finally:
        if created is not None:
            created.close()
    assert snapshot(store) == before and files(storage.order_directory) == disk and not calls


@contextmanager
def another_backend(assembly):
    store = assembly[0]
    schema = "phase4_other_" + uuid4().hex
    with store.engine.begin() as c:
        c.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(store.engine.url, connect_args={"options": "-csearch_path=" + schema})
    anchor = SharedWeightAnchorStore(engine)
    anchor.provision()
    try:
        yield anchor
    finally:
        engine.dispose()
        with store.engine.begin() as c:
            c.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


def unbind_for_test(assembly):
    store, c, *_ = assembly
    store.change(
        c.lease,
        "TEST_OLD_UNBOUND_ACCOUNT",
        lambda ledger: ledger.operations.pop(RUNTIME_STORAGE_KEY),
    )


def test_new_backend_with_same_scope_and_uuid_cannot_replace_account_binding(assembly):
    store, c, q, clock, w, source, sender, calls, storage, clocks = assembly
    w.reserve(1000, "RESCUE")
    with another_backend(assembly) as anchor:
        folder = storage.order_directory / "other-backend"
        folder.mkdir(mode=0o700)
        anchor.bootstrap(
            folder,
            storage.shared_scope,
            storage.shared_identity,
            monotonic=clock.mono,
            wall_clock=clock.utc,
            boot=clocks.boot,
        )
        clock.seconds += 70.01
        alternate = RuntimeStorage(
            storage.order_directory, folder, storage.shared_scope, storage.shared_identity, anchor
        )
        args = list(assembly)
        args[8] = alternate
        before, disk = snapshot(store), files(storage.order_directory)
        with pytest.raises(LocalQuotaStopped, match="STORAGE_BINDING"):
            open_session(args, allow_synthetic_model_execution=True)
        assert snapshot(store) == before and files(storage.order_directory) == disk and not calls


def test_live_anchor_object_cannot_silently_change_installations(assembly):
    store, c, q, clock, w, source, sender, calls, storage, clocks = assembly
    original = storage.shared_anchor
    original_id = original.installation_id()
    with another_backend(assembly) as alternate:
        assert original_id != alternate.installation_id()
        engine = original.engine
        try:
            original.engine = alternate.engine
            with pytest.raises(LocalQuotaStopped, match="INSTALLATION_CHANGED"):
                original.installation_id()
            with pytest.raises(LocalQuotaStopped, match="INSTALLATION_CHANGED"):
                w.view()
        finally:
            original.engine = engine
    assert original.installation_id() == original_id


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_installation_identity_is_database_immutable(assembly, operation):
    anchor = assembly[8].shared_anchor
    before = anchor.installation_id()
    with pytest.raises(DBAPIError, match="shared_weight_append_only"), anchor.engine.begin() as c:
        c.execute(
            installations.delete()
            if operation == "delete"
            else installations.update().values(identity=str(uuid4()))
        )
    assert anchor.installation_id() == before


def test_explicit_binding_is_fenced_idempotent_and_preserves_financial_state(assembly):
    store, c, q, clock, w, source, sender, calls, storage, clocks = assembly
    unbind_for_test(assembly)
    before = store.read(c.lease.account_id)
    disk = files(storage.order_directory)
    value = bind_runtime_storage(store, c.lease, storage, clocks=clocks)
    after = store.read(c.lease.account_id)
    assert after.operations[RUNTIME_STORAGE_KEY] == value
    expected = before.model_copy(deep=True)
    expected.operations[RUNTIME_STORAGE_KEY] = value
    assert after == expected and files(storage.order_directory) == disk and not calls
    raw = snapshot(store)
    assert bind_runtime_storage(store, c.lease, storage, clocks=clocks) == value
    assert snapshot(store) == raw and files(storage.order_directory) == disk


def test_explicit_binding_cannot_replace_an_existing_configuration(assembly):
    store, c, q, clock, w, source, sender, calls, storage, clocks = assembly
    alternate = RuntimeStorage(
        storage.order_directory,
        storage.shared_directory,
        "DEMO-another",
        uuid4(),
        storage.shared_anchor,
    )
    before, disk = snapshot(store), files(storage.order_directory)
    with pytest.raises(LocalQuotaStopped, match="STORAGE_BINDING_MISMATCH"):
        bind_runtime_storage(store, c.lease, alternate, clocks=clocks)
    assert snapshot(store) == before and files(storage.order_directory) == disk


@pytest.mark.parametrize("fault", ["pending", "wrong_fence", "live", "period"])
def test_unbound_account_refusals_leave_metadata_and_files_unchanged(assembly, monkeypatch, fault):
    store, c, q, clock, w, source, sender, calls, storage, clocks = assembly
    unbind_for_test(assembly)
    lease = c.lease
    if fault == "pending":
        w.reserve(1000, "RESCUE")
    if fault == "wrong_fence":
        lease = lease.model_copy(update={"fence": lease.fence + 1})
    if fault == "live":
        monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    if fault == "period":
        store.change(
            lease, "TEST_BAD_PERIOD", lambda ledger: setattr(ledger, "daily_key", "2026-01-01")
        )
    before, disk = snapshot(store), files(storage.order_directory)
    with pytest.raises(LocalQuotaStopped):
        bind_runtime_storage(store, lease, storage, clocks=clocks)
    assert snapshot(store) == before and files(storage.order_directory) == disk and not calls


def test_missing_binding_is_not_created_implicitly_by_assembly(assembly):
    store, c, q, clock, w, source, sender, calls, storage, clocks = assembly
    unbind_for_test(assembly)
    before, disk = snapshot(store), files(storage.order_directory)
    with pytest.raises(LocalQuotaStopped, match="STORAGE_BINDING_MISMATCH"):
        open_session(assembly, allow_synthetic_model_execution=True)
    assert snapshot(store) == before and files(storage.order_directory) == disk


def test_binding_removed_after_assembly_stops_run_before_http(assembly):
    store, c, q, clock, w, source, sender, calls, storage, clocks = assembly
    session = open_session(assembly, allow_synthetic_model_execution=True)
    clock.seconds += 70.01
    unbind_for_test(assembly)
    before, disk = snapshot(store), files(storage.order_directory)
    try:
        with pytest.raises(LocalQuotaStopped, match="STORAGE_BINDING_MISMATCH"):
            asyncio.run(session.run(uuid4()))
        assert snapshot(store) == before and files(storage.order_directory) == disk and not calls
    finally:
        session.close()


@pytest.mark.parametrize("kind", ["object", "scope", "anchor"])
def test_assembled_budget_rejects_provider_replacement_before_new_reservation(assembly, kind):
    store, c, q, clock, w, source, sender, calls, storage, clocks = assembly
    session = open_session(assembly, allow_synthetic_model_execution=True)
    budget = session.budget
    if kind == "object":
        budget.shared = w
    elif kind == "scope":
        budget.shared.scope = "DEMO-other"
    else:
        budget.shared.anchors = SharedWeightAnchorStore(storage.shared_anchor.engine)
    before, disk = snapshot(store), files(storage.order_directory)
    try:
        with pytest.raises(LocalQuotaStopped, match="PROVIDER_CHANGED"):
            budget("GET", "/fapi/v1/time", SecretStr(""), None)
        assert snapshot(store) == before and files(storage.order_directory) == disk and not calls
    finally:
        session.close()
