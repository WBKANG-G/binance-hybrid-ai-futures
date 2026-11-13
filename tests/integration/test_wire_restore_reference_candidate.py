"""Exact retained-reference checks on synthetic PostgreSQL/file restore fixtures."""

# ruff: noqa: F401,F811
import copy
import hashlib
import json
from uuid import uuid4

import pytest
from sqlalchemy import delete, event, insert, select, text, update

from packages.persistence.demo import accounts, outbox
from packages.persistence.demo import events as financial_events
from packages.persistence.demo_order_quota import LocalQuotaStopped
from packages.persistence.demo_wire_checkpoint_candidate import checkpoints
from services.execution.demo_wire_restore_reference_candidate import (
    create_reference_manifest,
    export_reference_copy,
    parse_reference,
    review_reference_file,
    review_restored_reference,
)
from tests.integration.test_demo_read_audit import audited
from tests.integration.test_response_evidence_candidate import evidenced
from tests.integration.test_runtime_read_accounting import financial
from tests.integration.test_shared_weight_checkpoint import anchored, demo_system
from tests.integration.test_wire_checkpoint_candidate import (
    SCOPE,
    capture,
    coupled,
    pg,
    review,
    run,
)
from tests.unit.test_quota_startup_read_only import files


def manifest(case):
    return create_reference_manifest(case[2], case[5], SCOPE, case[3].journal_id, "DEMO-test")


def compare(case, body, held=None):
    return review_restored_reference(
        case[2],
        case[5],
        SCOPE,
        case[3].journal_id,
        "DEMO-test",
        body,
        retained_sha256=held or hashlib.sha256(body).hexdigest(),
    )


def snapshot(case):
    with case[1].engine.connect() as c:
        row = dict(
            c.execute(select(accounts).where(accounts.c.account_id == "DEMO-test")).mappings().one()
        )
        event_ids = set(
            c.execute(
                select(financial_events.c.event_id).where(
                    financial_events.c.account_id == "DEMO-test"
                )
            ).scalars()
        )
    return dict(
        account=copy.deepcopy(row),
        event_ids=event_ids,
        checkpoint_rows=len(pg(case)),
        wire=case[3].path.read_bytes(),
        witness=case[2].witness(case[5]).read_bytes(),
    )


def restore_test_snapshot(case, backup):
    # Explicit destructive manipulation is confined to the disposable synthetic schema.
    with case[1].engine.begin() as c:
        c.execute(
            text(
                "ALTER TABLE wire_checkpoint_events_v2 DISABLE TRIGGER wire_checkpoint_immutable_v2"
            )
        )
        c.execute(delete(checkpoints).where(checkpoints.c.sequence >= backup["checkpoint_rows"]))
        c.execute(
            text(
                "ALTER TABLE wire_checkpoint_events_v2 ENABLE TRIGGER wire_checkpoint_immutable_v2"
            )
        )
        extra = select(financial_events.c.event_id).where(
            financial_events.c.account_id == "DEMO-test",
            financial_events.c.event_id.not_in(backup["event_ids"]),
        )
        c.execute(delete(outbox).where(outbox.c.event_id.in_(extra)))
        c.execute(
            delete(financial_events).where(
                financial_events.c.account_id == "DEMO-test",
                financial_events.c.event_id.not_in(backup["event_ids"]),
            )
        )
        c.execute(
            update(accounts).where(accounts.c.account_id == "DEMO-test").values(**backup["account"])
        )
    case[3].path.write_bytes(backup["wire"])
    case[2].witness(case[5]).write_bytes(backup["witness"])


def test_deterministic_snapshot_and_exact_match_are_readonly_and_grant_no_authority(coupled):
    run(coupled, capture(coupled))
    before, disk, rows = financial(coupled[0][0]), files(coupled[5]), pg(coupled)
    body = manifest(coupled)
    assert manifest(coupled) == body
    parsed = parse_reference(body)
    assert parsed["checkpoint"]["sequence"] == 4 and parsed["state"]["financial_prepares"] == 1
    report = compare(coupled, body)
    assert report["matches_supplied_retained_reference"] and not report["mismatch_fields"]
    assert "LOCAL_REQUEST_OUTCOMES_UNRESOLVED" in report["remaining_blockers"]
    for key in (
        "external_reference_authenticity_verified",
        "reference_is_latest_proven",
        "whole_system_rollback_protection_verified",
        "whole_runtime_backup_verified",
        "storage_restore_authorized",
        "restore_performed",
        "process_restart_resumption_supported",
        "credential_session_resumed",
        "execution_approved",
        "orders_enabled",
        "database_changed",
        "filesystem_changed",
    ):
        assert report[key] is False
    assert financial(coupled[0][0]) == before and files(coupled[5]) == disk and pg(coupled) == rows
    assert "synthetic-request-1" not in body.decode() and "payload" not in body.decode()


def test_no_digest_or_modified_reference_is_rejected_before_any_source_access(coupled):
    with pytest.raises(LocalQuotaStopped, match="SEPARATELY_RETAINED_DIGEST"):
        review_restored_reference(None, None, None, None, None, None)
    body = manifest(coupled)
    with pytest.raises(LocalQuotaStopped, match="RETAINED_DIGEST_MISMATCH"):
        review_restored_reference(None, None, None, None, None, body, retained_sha256="a" * 64)


def test_whole_selected_db_and_both_files_rollback_differs_from_latest_retained_digest(
    coupled, tmp_path_factory
):
    prepared = capture(coupled)
    old_body = manifest(coupled)
    old_state = snapshot(coupled)
    run(coupled, prepared)
    latest = manifest(coupled)
    ref_dir = tmp_path_factory.mktemp("separately-retained-reference")
    saved = export_reference_copy(
        coupled[2], coupled[5], ref_dir, SCOPE, coupled[3].journal_id, "DEMO-test", apply=True
    )
    reference_path = ref_dir / saved["filename"]
    assert reference_path.read_bytes() == latest
    restore_test_snapshot(coupled, old_state)
    assert review(coupled)["pg_file_checkpoint_matches"]
    assert manifest(coupled) == old_body
    disk, before = files(coupled[5]), financial(coupled[0][0])
    report = review_reference_file(
        coupled[2],
        coupled[5],
        SCOPE,
        coupled[3].journal_id,
        "DEMO-test",
        reference_path,
        retained_sha256=saved["retained_sha256"],
    )
    assert not report["matches_supplied_retained_reference"]
    assert "checkpoint.sequence" in report["mismatch_fields"]
    assert "financial.history_catalog_sha256" in report["mismatch_fields"]
    assert report["restore_reference_review"].startswith("BLOCKED")
    assert files(coupled[5]) == disk and financial(coupled[0][0]) == before
    assert reference_path.read_bytes() == latest
    stale = compare(coupled, old_body)
    assert stale["matches_supplied_retained_reference"]
    assert not stale["reference_is_latest_proven"] and not stale["storage_restore_authorized"]


@pytest.mark.parametrize("kind", ["payload", "same-length-payload", "fence", "timestamp", "remove"])
def test_history_not_bound_to_wire_checkpoints_is_still_covered(coupled, kind):
    body = manifest(coupled)
    with coupled[1].engine.begin() as c:
        row = (
            c.execute(
                select(financial_events)
                .where(financial_events.c.kind != "DEMO_SYNTHETIC_WIRE_PREPARED")
                .order_by(financial_events.c.event_id)
                .limit(1)
            )
            .mappings()
            .one()
        )
        where = financial_events.c.event_id == row["event_id"]
        if kind == "payload":
            c.execute(update(financial_events).where(where).values(payload=row["payload"] + " "))
        elif kind == "same-length-payload":
            c.execute(
                update(financial_events).where(where).values(payload="[" + row["payload"][1:])
            )
        elif kind == "fence":
            c.execute(update(financial_events).where(where).values(fence=row["fence"] + 1))
        elif kind == "timestamp":
            from datetime import timedelta

            c.execute(
                update(financial_events)
                .where(where)
                .values(as_of=row["as_of"] + timedelta(seconds=1))
            )
        else:
            c.execute(delete(outbox).where(outbox.c.event_id == row["event_id"]))
            c.execute(delete(financial_events).where(where))
    assert review(coupled)["pg_file_checkpoint_matches"]
    report = compare(coupled, body)
    assert not report["matches_supplied_retained_reference"]
    assert "financial.history_catalog_sha256" in report["mismatch_fields"]


def test_missing_unbound_history_outbox_fails_catalog_integrity(coupled):
    with coupled[1].engine.begin() as c:
        key = c.execute(select(financial_events.c.event_id).limit(1)).scalar_one()
        c.execute(delete(outbox).where(outbox.c.event_id == key))
    with pytest.raises(LocalQuotaStopped, match="HISTORY_METADATA_INVALID"):
        manifest(coupled)


@pytest.mark.parametrize("kind", ["state", "owner", "lease"])
def test_account_state_and_owner_metadata_are_part_of_reference(coupled, kind):
    body = manifest(coupled)
    with coupled[1].engine.begin() as c:
        row = c.execute(select(accounts)).mappings().one()
        if kind == "state":
            state = copy.deepcopy(row["state"])
            state["paused"] = False
            c.execute(update(accounts).values(state=state))
        elif kind == "owner":
            c.execute(update(accounts).values(owner=str(uuid4()), fence=row["fence"] + 1))
        else:
            from datetime import timedelta

            c.execute(
                update(accounts).values(lease_until=row["lease_until"] + timedelta(seconds=1))
            )
    report = compare(coupled, body)
    assert not report["matches_supplied_retained_reference"]
    assert "financial.account_row_sha256" in report["mismatch_fields"]


def test_pending_unknown_snapshot_is_forensic_only(coupled):
    from integrations.binance_usdm.rest import RESTResponse
    from services.execution.demo_wire_session_contract_candidate import SyntheticResponder

    with pytest.raises(LocalQuotaStopped, match="UNCERTAIN"):
        run(coupled, capture(coupled), SyntheticResponder(RESTResponse(200, b"{}", {}), fail=True))
    body = manifest(coupled)
    assert parse_reference(body)["state"]["pending_operations"] == 1
    report = compare(coupled, body)
    assert report["matches_supplied_retained_reference"]
    assert "UNRESOLVED_DISPATCH_PRESENT" in report["remaining_blockers"]
    assert not report["storage_restore_authorized"]


def test_export_is_explicit_private_idempotent_and_not_external_custody(coupled, tmp_path_factory):
    with pytest.raises(LocalQuotaStopped, match="EXPLICIT_COPY"):
        export_reference_copy(None, None, None, None, None, None)
    root = tmp_path_factory.mktemp("private-reference-copy")
    before, disk = financial(coupled[0][0]), files(coupled[5])
    one = export_reference_copy(
        coupled[2], coupled[5], root, SCOPE, coupled[3].journal_id, "DEMO-test", apply=True
    )
    two = export_reference_copy(
        coupled[2], coupled[5], root, SCOPE, coupled[3].journal_id, "DEMO-test", apply=True
    )
    assert one["reference_copy_created"] and not two["reference_copy_created"]
    assert not one["reference_external_custody_verified"] and not one["reference_is_latest_proven"]
    path = root / one["filename"]
    assert path.stat().st_mode & 0o777 == 0o600
    assert hashlib.sha256(path.read_bytes()).hexdigest() == one["retained_sha256"]
    assert financial(coupled[0][0]) == before and files(coupled[5]) == disk


@pytest.mark.parametrize("kind", ["tamper", "symlink", "public-file", "source-directory"])
def test_bad_reference_copies_are_not_repaired_or_used_as_fallback(coupled, tmp_path_factory, kind):
    root = tmp_path_factory.mktemp("unsafe-reference-test")
    saved = export_reference_copy(
        coupled[2], coupled[5], root, SCOPE, coupled[3].journal_id, "DEMO-test", apply=True
    )
    path = root / saved["filename"]
    if kind == "tamper":
        path.write_bytes(b"{}")
    elif kind == "symlink":
        path.unlink()
        path.symlink_to(root / "missing")
    elif kind == "public-file":
        path.chmod(0o644)
    else:
        with pytest.raises(LocalQuotaStopped, match="SEPARATE_DIRECTORY"):
            export_reference_copy(
                coupled[2],
                coupled[5],
                coupled[5],
                SCOPE,
                coupled[3].journal_id,
                "DEMO-test",
                apply=True,
            )
        return
    before = path.lstat()
    with pytest.raises((LocalQuotaStopped, OSError)):
        export_reference_copy(
            coupled[2], coupled[5], root, SCOPE, coupled[3].journal_id, "DEMO-test", apply=True
        )
    assert path.lstat() == before
    with pytest.raises((LocalQuotaStopped, OSError)):
        review_reference_file(
            coupled[2],
            coupled[5],
            SCOPE,
            coupled[3].journal_id,
            "DEMO-test",
            path,
            retained_sha256=saved["retained_sha256"],
        )


@pytest.mark.parametrize(
    "limit",
    ["MAX_HISTORY_ROWS", "MAX_HISTORY_BYTES", "MAX_HISTORY_ROW_BYTES", "MAX_HISTORY_SECONDS"],
)
def test_catalog_limits_deny_before_content_hash_queries(coupled, monkeypatch, limit):
    import services.execution.demo_wire_restore_reference_candidate as module

    monkeypatch.setattr(module, limit, -1)
    seen = []

    def capture_sql(c, cursor, statement, params, context, many):
        seen.append(statement)

    event.listen(coupled[1].engine, "before_cursor_execute", capture_sql)
    before = financial(coupled[0][0])
    try:
        with pytest.raises(LocalQuotaStopped, match="HISTORY_(PREFLIGHT_LIMIT|TIME_BUDGET)"):
            manifest(coupled)
    finally:
        event.remove(coupled[1].engine, "before_cursor_execute", capture_sql)
    assert not any("AS body_sha256" in sql for sql in seen)
    assert financial(coupled[0][0]) == before


def test_account_limit_denies_before_reference_creation(coupled, monkeypatch):
    import services.execution.demo_wire_restore_reference_candidate as module

    monkeypatch.setattr(module, "MAX_ACCOUNT_BYTES", 1)
    with pytest.raises(LocalQuotaStopped, match="ACCOUNT_PREFLIGHT_LIMIT"):
        manifest(coupled)


def test_large_history_hashes_all_3802_rows_without_returning_their_bodies(coupled):
    engine = coupled[1].engine
    with engine.begin() as c:
        c.execute(
            text("""INSERT INTO demo_events(event_id,account_id,fence,as_of,kind,payload)
            SELECT md5('SYNTHETIC-REFERENCE-CATALOG-' || n::text)::uuid::text,
                'DEMO-test',1,:stamp,'REFERENCE_SYNTHETIC_CATALOG',
                json_build_object('synthetic_only', repeat('x',75000))::text
            FROM generate_series(1,3802) AS n"""),
            dict(stamp=coupled[1].now()),
        )
        c.execute(
            text("""INSERT INTO demo_outbox(event_id,topic)
            SELECT event_id,'DEMO_AUDIT' FROM demo_events WHERE kind='REFERENCE_SYNTHETIC_CATALOG'""")
        )
    returned_columns = []

    def capture_description(c, cursor, statement, params, context, many):
        if "AS body_sha256" in statement:
            returned_columns.append([item.name for item in cursor.description])

    event.listen(engine, "after_cursor_execute", capture_description)
    try:
        body = manifest(coupled)
    finally:
        event.remove(engine, "after_cursor_execute", capture_description)
    data = parse_reference(body)
    assert data["financial"]["history_rows"] >= 3802
    assert data["financial"]["history_bytes"] >= 285150000
    assert len(body) <= 8192
    assert len(returned_columns) >= 60
    assert all(
        cols == ["event_id", "fence", "as_of", "kind", "body_bytes", "body_sha256", "topic"]
        for cols in returned_columns
    )
    assert "synthetic_only" not in body.decode()


@pytest.mark.parametrize("failure", ["partial", "directory-fsync"])
def test_reference_copy_failure_has_no_success_or_automatic_repair(
    coupled, tmp_path_factory, monkeypatch, failure
):
    import services.execution.demo_wire_restore_reference_candidate as module

    root = tmp_path_factory.mktemp("reference-export-fault")
    before = financial(coupled[0][0])
    disk = files(coupled[5])

    def fail(*args):
        if failure == "partial":
            path, body = args
            path.write_bytes(body[:7])
            path.chmod(0o600)
        raise OSError("SYNTHETIC_REFERENCE_COPY_FAILURE")

    monkeypatch.setattr(module, "_write_new" if failure == "partial" else "_sync_directory", fail)
    with pytest.raises(OSError, match="REFERENCE_COPY_FAILURE"):
        export_reference_copy(
            coupled[2], coupled[5], root, SCOPE, coupled[3].journal_id, "DEMO-test", apply=True
        )
    assert len(list(root.iterdir())) == 1
    if failure == "partial":
        partial = list(root.iterdir())[0].read_bytes()
        with pytest.raises(LocalQuotaStopped, match="EXISTING_COPY_MISMATCH"):
            export_reference_copy(
                coupled[2], coupled[5], root, SCOPE, coupled[3].journal_id, "DEMO-test", apply=True
            )
        assert list(root.iterdir())[0].read_bytes() == partial
    assert financial(coupled[0][0]) == before and files(coupled[5]) == disk


@pytest.mark.parametrize("rollback", [False, True])
def test_separate_process_compares_retained_digest_and_never_reopens_session(
    coupled, tmp_path_factory, rollback
):
    import os
    import subprocess
    import sys

    prepared = capture(coupled)
    old = snapshot(coupled)
    run(coupled, prepared)
    root = tmp_path_factory.mktemp("process-reference-copy")
    saved = export_reference_copy(
        coupled[2], coupled[5], root, SCOPE, coupled[3].journal_id, "DEMO-test", apply=True
    )
    if rollback:
        restore_test_snapshot(coupled, old)
    with coupled[1].engine.connect() as c:
        schema = c.execute(text("SELECT current_schema()")).scalar_one()
    script = """
import json, os
from pathlib import Path
from uuid import UUID
from sqlalchemy import create_engine
from packages.persistence.demo_wire_checkpoint_candidate import WireCheckpointStore
from services.execution.demo_wire_restore_reference_candidate import review_reference_file
engine=create_engine(os.environ["TEST_DATABASE_URL"],connect_args={"options":"-csearch_path="+os.environ["WIRE_TEST_SCHEMA"]})
result=review_reference_file(WireCheckpointStore(engine),Path(os.environ["WIRE_TEST_SOURCE"]),"DEMO-wire-scope",UUID(os.environ["WIRE_TEST_JOURNAL"]),"DEMO-test",Path(os.environ["WIRE_TEST_REFERENCE"]),retained_sha256=os.environ["WIRE_TEST_RETAINED_HASH"])
print(json.dumps({"matches":result["matches_supplied_retained_reference"],"restore":result["restore_performed"],"latest":result["reference_is_latest_proven"],"resume":result["process_restart_resumption_supported"]}))
engine.dispose()
"""
    env = dict(
        os.environ,
        WIRE_TEST_SCHEMA=schema,
        WIRE_TEST_SOURCE=str(coupled[5]),
        WIRE_TEST_JOURNAL=str(coupled[3].journal_id),
        WIRE_TEST_REFERENCE=str(root / saved["filename"]),
        WIRE_TEST_RETAINED_HASH=saved["retained_sha256"],
    )
    before, disk = financial(coupled[0][0]), files(coupled[5])
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=20,
    )
    assert json.loads(result.stdout) == dict(
        matches=not rollback, restore=False, latest=False, resume=False
    )
    assert financial(coupled[0][0]) == before and files(coupled[5]) == disk
