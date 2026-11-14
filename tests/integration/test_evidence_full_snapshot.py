"""Detect V2 data and DDL changes omitted by the historical demo_ guard."""

# ruff: noqa: F401,F811
import hashlib

import pytest
from sqlalchemy import text, update

from packages.persistence.demo_order_quota import LocalQuotaStopped
from packages.persistence.demo_response_evidence import bindings
from scripts.review_demo_read_code_install import preserved_snapshot
from services.execution.demo_evidence_snapshot import evidence_snapshot
from tests.integration.test_demo_read_audit import audited
from tests.integration.test_read_audit_bootstrap import ready
from tests.integration.test_response_evidence_bootstrap import setup_ready
from tests.integration.test_response_evidence_candidate import evidenced, review, transport
from tests.integration.test_runtime_read_accounting import financial, signed
from tests.integration.test_shared_weight_checkpoint import anchored, demo_system
from tests.unit.test_quota_startup_read_only import files


def snap(case):
    a = case[0]
    return evidence_snapshot(a[1].engine, a[3])


def test_stable_snapshot_is_read_only_covers_both_v2_tables_and_private_witness(evidenced):
    a = evidenced[0]
    transport(evidenced)(*signed())
    disk, account = files(a[3]), financial(a)
    first = snap(evidenced)
    assert first == snap(evidenced)
    assert first["tables"]["read_response_evidence_bindings_v2"]["rows"] == 1
    assert first["tables"]["read_response_evidence_events_v2"]["rows"] == 2
    assert len(first["catalog"]["functions"]["sha256"]) == 64
    assert any(p.endswith(".response-evidence-v2.log") for p in first["files"])
    assert not first["recoverable_backup"] and not first["cross_store_atomic"]
    assert files(a[3]) == disk and financial(a) == account
    # No account values, raw response content or function definitions in the report.
    assert "SYNTHETIC" not in str(first) and "RAISE EXCEPTION" not in str(first)


@pytest.mark.parametrize(
    "fault",
    ["v2_row", "function", "trigger", "column", "constraint", "index", "extra_table", "enum"],
)
def test_full_guard_detects_changes_hidden_from_legacy_prefix_guard(evidenced, fault):
    a = evidenced[0]
    before, legacy = snap(evidenced), preserved_snapshot(a[1].engine, a[3])
    with a[1].engine.begin() as c:
        if fault == "v2_row":
            c.execute(
                text(
                    "ALTER TABLE read_response_evidence_bindings_v2 DISABLE TRIGGER response_evidence_immutable_v2"
                )
            )
            c.execute(update(bindings).values(account="DEMO-other"))
            c.execute(
                text(
                    "ALTER TABLE read_response_evidence_bindings_v2 ENABLE TRIGGER response_evidence_immutable_v2"
                )
            )
        else:
            c.execute(
                text(
                    {
                        "function": "CREATE OR REPLACE FUNCTION read_response_evidence_immutable_v2() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$",
                        "trigger": "ALTER TABLE read_response_evidence_events_v2 DISABLE TRIGGER response_evidence_immutable_v2",
                        "column": "ALTER TABLE read_response_evidence_events_v2 ADD COLUMN extra TEXT",
                        "constraint": "ALTER TABLE read_response_evidence_events_v2 DROP CONSTRAINT read_response_evidence_events_v2_scope_fkey",
                        "index": "CREATE INDEX evidence_extra_idx ON read_response_evidence_events_v2 (phase)",
                        "extra_table": "CREATE TABLE unprefixed_extra (id integer PRIMARY KEY)",
                        "enum": "CREATE TYPE evidence_enum AS ENUM ('ONE','TWO')",
                    }[fault]
                )
            )
    assert snap(evidenced)["sha256"] != before["sha256"]
    assert preserved_snapshot(a[1].engine, a[3]) == legacy


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE SEQUENCE unsupported_sequence",
        "CREATE UNLOGGED TABLE unsupported_unlogged (id integer PRIMARY KEY)",
        "CREATE TABLE unsupported_no_pk (id integer)",
        "CREATE VIEW unsupported_view AS SELECT 1 AS id",
        "CREATE DOMAIN unsupported_domain AS integer CHECK (VALUE>0)",
    ],
)
def test_unsupported_objects_are_not_silently_omitted(evidenced, sql):
    a = evidenced[0]
    with a[1].engine.begin() as c:
        c.execute(text(sql))
    before = files(a[3])
    with pytest.raises(LocalQuotaStopped, match="UNSUPPORTED|PRIMARY_KEY_REQUIRED"):
        snap(evidenced)
    assert files(a[3]) == before


def test_witness_only_change_is_detected_without_returning_contents(evidenced):
    before = snap(evidenced)
    path = evidenced[2].witness(evidenced[0][3], evidenced[3].scope)
    path.write_bytes(path.read_bytes() + b"PRIVATE_SENTINEL")
    after = snap(evidenced)
    assert before["sha256"] != after["sha256"] and before["tables"] == after["tables"]
    assert "PRIVATE_SENTINEL" not in str(after)


def test_expired_budget_blocks_without_any_change(evidenced):
    before = files(evidenced[0][3])
    with pytest.raises(LocalQuotaStopped, match="TIME_LIMIT"):
        evidence_snapshot(evidenced[0][1].engine, evidenced[0][3], seconds=1e-9)
    assert files(evidenced[0][3]) == before


@pytest.mark.parametrize("partial", [False, True])
def test_full_snapshot_checks_bootstrap_failure_boundary(setup_ready, monkeypatch, partial):
    import packages.persistence.demo_response_evidence as module
    from tests.integration.test_response_evidence_bootstrap import call

    a = setup_ready
    before = evidence_snapshot(a[1].engine, a[3])
    original = module._write_new

    def fail(*args):
        if partial:
            original(*args)
        raise OSError("SYNTHETIC_BOOTSTRAP_BOUNDARY")

    monkeypatch.setattr(module, "_write_new", fail)
    with pytest.raises(OSError, match="SYNTHETIC_BOOTSTRAP"):
        call(a, apply=True, readers_stopped=True)
    after = evidence_snapshot(a[1].engine, a[3])
    assert after["tables"] == before["tables"] and after["catalog"] == before["catalog"]
    assert set(after["files"]) - set(before["files"]) == (
        {module.ResponseEvidenceStore.witness(a[3], a[0].scope).name} if partial else set()
    )
    assert all(after["files"][p] == h for p, h in before["files"].items())
    if partial:
        with pytest.raises(LocalQuotaStopped, match="ORPHAN_WITNESS"):
            call(a, apply=True, readers_stopped=True)
        assert evidence_snapshot(a[1].engine, a[3]) == after
    else:
        assert after == before


def test_full_snapshot_checks_exact_auxiliary_bootstrap_delta(setup_ready):
    from packages.persistence.demo_response_evidence import ResponseEvidenceStore
    from tests.integration.test_response_evidence_bootstrap import call

    a = setup_ready
    before = evidence_snapshot(a[1].engine, a[3])
    call(a, apply=True, readers_stopped=True)
    after = evidence_snapshot(a[1].engine, a[3])
    assert set(after["tables"]) - set(before["tables"]) == {
        "read_response_evidence_bindings_v2",
        "read_response_evidence_events_v2",
    }
    assert all(after["tables"][p] == v for p, v in before["tables"].items())
    assert after["tables"]["read_response_evidence_bindings_v2"]["rows"] == 1
    assert after["tables"]["read_response_evidence_events_v2"]["rows"] == 0
    with a[1].engine.connect() as c:
        # PG18 exposes NOT NULL constraints in pg_constraint; earlier versions
        # expose these through pg_attribute, which is also captured.
        not_null_constraints = (
            21 if int(c.execute(text("SHOW server_version_num")).scalar_one()) >= 180000 else 0
        )
    for section, data in before["catalog"].items():
        assert all(after["catalog"][section]["objects"][k] == h for k, h in data["objects"].items())
    for section, delta in [
        ("functions", 1),
        ("triggers", 2),
        ("indexes", 2),
        ("constraints", 5 + not_null_constraints),
        ("relations", 2),
    ]:
        assert after["catalog"][section]["count"] - before["catalog"][section]["count"] == delta
    assert set(after["files"]) - set(before["files"]) == {
        ResponseEvidenceStore.witness(a[3], a[0].scope).name
    }
    assert all(after["files"][p] == h for p, h in before["files"].items())


def test_row_size_guard_and_quoted_names(evidenced, monkeypatch):
    import services.execution.demo_evidence_snapshot as module

    a = evidenced[0]
    with a[1].engine.begin() as c:
        c.execute(text('CREATE TABLE "quoted""table" (id integer PRIMARY KEY, body text)'))
        c.execute(
            text('INSERT INTO "quoted""table" VALUES (1,:body)'),
            dict(body="SYNTHETIC_LARGE_BODY" * 100),
        )
    assert snap(evidenced)["tables"]['quoted"table']["rows"] == 1
    monkeypatch.setattr(module, "MAX_ROW_BYTES", 32)
    before = files(a[3])
    with pytest.raises(LocalQuotaStopped, match="ROW_LIMIT"):
        snap(evidenced)
    assert files(a[3]) == before


@pytest.mark.parametrize(
    "sql",
    [
        "ALTER TABLE read_response_evidence_events_v2 ENABLE ROW LEVEL SECURITY",
        "CREATE TABLE unsupported_child (id integer PRIMARY KEY) INHERITS (read_response_evidence_bindings_v2)",
    ],
)
def test_hidden_rows_and_inheritance_do_not_yield_complete_fingerprint(evidenced, sql):
    a = evidenced[0]
    with a[1].engine.begin() as c:
        c.execute(text(sql))
    before = files(a[3])
    with pytest.raises(LocalQuotaStopped, match="UNSUPPORTED"):
        snap(evidenced)
    assert files(a[3]) == before


def test_table_rewrite_rules_are_fingerprinted(evidenced):
    a = evidenced[0]
    before = snap(evidenced)
    with a[1].engine.begin() as c:
        c.execute(
            text(
                "CREATE RULE evidence_delete_rule AS ON DELETE TO read_response_evidence_events_v2 DO INSTEAD NOTHING"
            )
        )
    after = snap(evidenced)
    assert after["sha256"] != before["sha256"]
    assert after["catalog"]["rules"]["count"] == before["catalog"]["rules"]["count"] + 1
    assert after["tables"] == before["tables"]
