"""Real PostgreSQL transaction tests restricted to synthetic pg_temp relations."""

import json
import re
import subprocess
from pathlib import Path

import pytest

from tests.unit import test_typed_draft_bundle as fixtures
from tests.unit.test_typed_draft_bundle import before_state

ROOT = Path(__file__).resolve().parents[2]
SQL = ROOT / "tools/windows_demo_atomic_rehearsal/transaction.sql"
OP = "f10d7631-6ef9-44a7-b423-c964e56ae6cd"
FIELDS = {
    "daily_key": "2026-11-18",
    "weekly_key": "2026-11-16",
    "daily_base": "4998.74183608",
    "weekly_base": "4998.74183608",
    "reconciled": False,
    "stream_ready": False,
}


@pytest.fixture(scope="module")
def database():
    yield from fixtures.isolated_model_worker_database.__wrapped__()


def literal(value):
    return "'" + json.dumps(value).replace("'", "''") + "'::jsonb"


def setup():
    return (
        SQL.read_text()
        + "\nINSERT INTO pg_temp.period_accounts VALUES "
        + "('DEMO-SYNTHETIC-PERIOD-TRANSACTION',NULL,110,NULL,"
        + literal(before_state())
        + ");\n"
        + "CREATE TEMP TABLE original AS SELECT * FROM pg_temp.period_accounts;\n"
    )


def invoke(fault="NONE", digest=None, fence="110", fields=None, evidence="e" * 64):
    digest = (
        digest
        or "(SELECT encode(sha256(convert_to(state::text,'UTF8')),'hex') FROM pg_temp.original)"
    )
    return (
        f"pg_temp.rehearse_period('{OP}', {digest}, {fence}, "
        + literal(FIELDS if fields is None else fields)
        + f", '{evidence}', '{fault}')"
    )


def run(args, sql):
    result = subprocess.run(args, input=sql, text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_atomic_success_and_exact_idempotent_retry(database):
    _, _, args = database
    sql = setup() + "SELECT " + invoke() + ";\nSELECT " + invoke() + ";\n"
    sql += f"""
    DO $test$
    DECLARE b jsonb; a jsonb; r jsonb;
    BEGIN
      SELECT state INTO b FROM pg_temp.original;
      SELECT state INTO a FROM pg_temp.period_accounts;
      r := (a->'operations'->>'{OP}')::jsonb;
      IF (a - ARRAY['daily_key','weekly_key','daily_base','weekly_base',
          'reconciled','stream_ready','operations']) IS DISTINCT FROM
         (b - ARRAY['daily_key','weekly_key','daily_base','weekly_base',
          'reconciled','stream_ready','operations'])
         OR ((a->'operations') - '{OP}') IS DISTINCT FROM b->'operations'
         OR (SELECT fence FROM pg_temp.period_accounts) <> 111
         OR (SELECT count(*) FROM pg_temp.period_events) <> 1
         OR (SELECT count(*) FROM pg_temp.period_outbox) <> 1
         OR r->'simulation_only' <> 'true'::jsonb
         OR r->'applied' <> 'false'::jsonb OR r->'source_accepted' <> 'false'::jsonb
         OR a->'paused' <> 'true'::jsonb THEN
        RAISE EXCEPTION 'TEST_PRESERVATION_FAILED';
      END IF;
    END $test$;
    """
    assert run(args, sql).splitlines() == ["SIMULATED", "ALREADY_SIMULATED"]


@pytest.mark.parametrize("fault", ["AFTER_ACCOUNT", "AFTER_EVENT", "AFTER_OUTBOX"])
def test_each_write_failure_rolls_back_entire_unit(database, fault):
    _, _, args = database
    sql = (
        setup()
        + f"""
    DO $test$
    BEGIN
      BEGIN
        PERFORM {invoke(fault)};
        RAISE EXCEPTION 'TEST_EXPECTED_FAILURE';
      EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'SIMULATION_INJECTED_FAILURE' THEN RAISE; END IF;
      END;
      IF EXISTS ((SELECT * FROM pg_temp.original) EXCEPT (SELECT * FROM pg_temp.period_accounts))
         OR EXISTS (SELECT 1 FROM pg_temp.period_events)
         OR EXISTS (SELECT 1 FROM pg_temp.period_outbox) THEN
        RAISE EXCEPTION 'TEST_PARTIAL_WRITE';
      END IF;
    END $test$;
    SELECT 'ROLLED_BACK';
    """
    )
    assert run(args, sql) == "ROLLED_BACK"


@pytest.mark.parametrize(
    "mutation,call,error",
    [
        ("", invoke(digest="'" + "0" * 64 + "'"), "SIMULATION_STALE_PREVIEW"),
        ("", invoke(fence="109"), "SIMULATION_STALE_PREVIEW"),
        (
            "UPDATE pg_temp.period_accounts SET owner='active';",
            invoke(),
            "SIMULATION_WRITER_NOT_QUIESCENT",
        ),
        (
            "UPDATE pg_temp.period_accounts SET lease_until=clock_timestamp()+interval '60 seconds';",
            invoke(),
            "SIMULATION_WRITER_NOT_QUIESCENT",
        ),
        ("", invoke(fields={**FIELDS, "daily_base": "5000.00000000"}), "SIMULATION_INPUT_INVALID"),
        ("", invoke(fields={**FIELDS, "stream_ready": True}), "SIMULATION_INPUT_INVALID"),
        ("", invoke(fence="9223372036854775807"), "SIMULATION_INPUT_INVALID"),
        ("", invoke(evidence="not-a-hash"), "SIMULATION_INPUT_INVALID"),
    ],
)
def test_guards_write_nothing(database, mutation, call, error):
    _, _, args = database
    sql = setup() + mutation + "CREATE TEMP TABLE at_call AS SELECT * FROM pg_temp.period_accounts;"
    sql += f"""
    DO $test$ BEGIN
      BEGIN
        PERFORM {call}; RAISE EXCEPTION 'TEST_EXPECTED_FAILURE';
      EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> '{error}' THEN RAISE; END IF;
      END;
      IF EXISTS ((SELECT * FROM pg_temp.at_call) EXCEPT (SELECT * FROM pg_temp.period_accounts))
        OR EXISTS (SELECT 1 FROM pg_temp.period_events)
        OR EXISTS (SELECT 1 FROM pg_temp.period_outbox) THEN
        RAISE EXCEPTION 'TEST_GUARD_WROTE_DATA';
      END IF;
    END $test$; SELECT 'REFUSED';
    """
    assert run(args, sql) == "REFUSED"


@pytest.mark.parametrize(
    "tamper",
    [
        "DELETE FROM pg_temp.period_outbox;",
        "UPDATE pg_temp.period_events SET kind='WRONG';",
        "UPDATE pg_temp.period_accounts SET fence=112;",
        "UPDATE pg_temp.period_accounts SET state=jsonb_set(state,'{high_water}','\"0\"'::jsonb);",
        "UPDATE pg_temp.period_accounts SET state=jsonb_set(state,'{operations,old}','\"CHANGED\"'::jsonb);",
    ],
)
def test_retry_checks_full_record_not_just_operation_key(database, tamper):
    _, _, args = database
    sql = setup() + f"SELECT {invoke()};" + tamper
    sql += f"""
    DO $test$ BEGIN
      BEGIN
        PERFORM {invoke()}; RAISE EXCEPTION 'TEST_EXPECTED_FAILURE';
      EXCEPTION WHEN raise_exception THEN
        IF SQLERRM <> 'SIMULATION_OPERATION_CONFLICT' THEN RAISE; END IF;
      END;
    END $test$; SELECT 'CONFLICT_REFUSED';
    """
    assert run(args, sql).splitlines() == ["SIMULATED", "CONFLICT_REFUSED"]


def test_event_insert_constraint_failure_undoes_account_update(database):
    _, _, args = database
    sql = (
        setup()
        + f"""
    INSERT INTO pg_temp.period_events VALUES
      ('{OP}','DEMO-SYNTHETIC-PERIOD-TRANSACTION',110,'EXISTING','{{}}'::jsonb);
    DO $test$ BEGIN
      BEGIN
        PERFORM {invoke()}; RAISE EXCEPTION 'TEST_EXPECTED_FAILURE';
      EXCEPTION WHEN unique_violation THEN NULL;
      END;
      IF EXISTS ((SELECT * FROM pg_temp.original) EXCEPT (SELECT * FROM pg_temp.period_accounts))
        OR EXISTS (SELECT 1 FROM pg_temp.period_outbox)
        OR (SELECT count(*) FROM pg_temp.period_events) <> 1 THEN
        RAISE EXCEPTION 'TEST_PARTIAL_CONSTRAINT_WRITE';
      END IF;
    END $test$; SELECT 'CONSTRAINT_ROLLED_BACK';
    """
    )
    assert run(args, sql) == "CONSTRAINT_ROLLED_BACK"


def test_caller_rollback_cancels_successful_function(database):
    _, _, args = database
    sql = setup() + f"BEGIN; SELECT {invoke()}; ROLLBACK;"
    sql += """
    DO $test$ BEGIN
      IF EXISTS ((SELECT * FROM pg_temp.original) EXCEPT (SELECT * FROM pg_temp.period_accounts))
        OR EXISTS (SELECT 1 FROM pg_temp.period_events)
        OR EXISTS (SELECT 1 FROM pg_temp.period_outbox) THEN
        RAISE EXCEPTION 'TEST_CALLER_ROLLBACK_FAILED';
      END IF;
    END $test$; SELECT 'CALLER_ROLLED_BACK';
    """
    assert run(args, sql).splitlines() == ["SIMULATED", "CALLER_ROLLED_BACK"]


def test_sql_has_no_persistent_targets_or_network_calls():
    sql = SQL.read_text()
    assert "public." not in sql and "demo_accounts" not in sql
    assert "CREATE TEMP TABLE" in sql and "CREATE FUNCTION pg_temp." in sql
    targets = re.findall(
        r"(?:UPDATE|INSERT INTO|(?<!DISTINCT )FROM|JOIN)\s+([a-z_][a-z_0-9.]*)",
        sql,
        flags=re.IGNORECASE,
    )
    assert targets and all(target.startswith("pg_temp.") for target in targets)
    assert "FOR UPDATE" in sql and "GET DIAGNOSTICS changed = ROW_COUNT" in sql
