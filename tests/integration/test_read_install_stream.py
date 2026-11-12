"""Complete bounded-memory fingerprints, including a real old-query timeout."""

# ruff: noqa: F811
import os
import time
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError

from packages.persistence.demo_order_quota import LocalQuotaStopped
from scripts import review_demo_read_code_install as guard
from tests.integration.test_demo import system as demo_system  # noqa: F401


@pytest.fixture
def stream_case(demo_system, tmp_path):
    engine = demo_system[0].engine
    with engine.connect() as c:
        schema = c.execute(text("SELECT current_schema()")).scalar_one()
    root = tmp_path / "storage"
    root.mkdir(mode=0o700)
    path = root / "existing.lock"
    path.write_bytes(b"preserved")
    path.chmod(0o600)
    reader = create_engine(
        os.environ["TEST_DATABASE_URL"],
        connect_args={"options": f"-csearch_path={schema} -cstatement_timeout=2000"},
    )
    yield engine, reader, root, schema
    reader.dispose()


def test_large_total_history_is_hashed_completely_beyond_old_32m_limit(stream_case):
    admin, reader, root, _ = stream_case
    with admin.begin() as c:
        c.execute(
            text("CREATE TABLE demo_large_history (id integer PRIMARY KEY, payload text NOT NULL)")
        )
        c.execute(
            text(
                "INSERT INTO demo_large_history SELECT n, repeat('X',524288) FROM generate_series(1,80) n"
            )
        )
    with reader.connect() as c, c.begin():
        c.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        table = guard._table_snapshot(c, inspect(c), "demo_large_history", time.monotonic() + 60)
        assert c.execute(text("SHOW statement_timeout")).scalar_one() == "2s"
    assert table["row_count"] == 80 and table["row_bytes"] > 32 * 1024 * 1024
    before = guard.preserved_snapshot(reader, root)
    with admin.begin() as c:
        c.execute(text("UPDATE demo_large_history SET payload=repeat('Y',524288) WHERE id=80"))
    assert guard.preserved_snapshot(reader, root) != before  # final row was never omitted


def test_history_after_old_10000_row_limit_is_not_truncated(stream_case):
    admin, reader, root, _ = stream_case
    with admin.begin() as c:
        c.execute(text("CREATE TABLE demo_long_history (id integer PRIMARY KEY, value text)"))
        c.execute(
            text(
                "INSERT INTO demo_long_history SELECT n,'original' FROM generate_series(1,10001) n"
            )
        )
    with reader.connect() as c, c.begin():
        table = guard._table_snapshot(c, inspect(c), "demo_long_history", time.monotonic() + 60)
    assert table["row_count"] == 10001
    before = guard.preserved_snapshot(reader, root)
    with admin.begin() as c:
        c.execute(text("UPDATE demo_long_history SET value='changed' WHERE id=10001"))
    assert guard.preserved_snapshot(reader, root) != before


def test_composite_native_key_order_and_bound_quoted_values_are_deterministic(stream_case):
    admin, reader, root, _ = stream_case
    with admin.begin() as c:
        c.execute(
            text(
                "CREATE TABLE demo_composite (scope text, seq bigint, value text, PRIMARY KEY(scope,seq))"
            )
        )
        for scope, seq in [("b'quoted", 2), ("a", 20), ("b'quoted", 1), ("가", 3)]:
            c.execute(
                text("INSERT INTO demo_composite VALUES (:scope,:seq,'same')"),
                dict(scope=scope, seq=seq),
            )
    before = guard.preserved_snapshot(reader, root)
    with admin.begin() as c:
        rows = c.execute(text("SELECT * FROM demo_composite ORDER BY scope,seq")).mappings().all()
        c.execute(text("DELETE FROM demo_composite"))
        for row in reversed(rows):
            c.execute(text("INSERT INTO demo_composite VALUES (:scope,:seq,:value)"), dict(row))
    assert guard.preserved_snapshot(reader, root) == before


def test_missing_primary_key_is_refused_without_excluding_table(stream_case):
    admin, reader, root, _ = stream_case
    with admin.begin() as c:
        c.execute(text("CREATE TABLE demo_no_key(value text)"))
    with pytest.raises(LocalQuotaStopped, match="PRIMARY_KEY_REQUIRED"):
        guard.preserved_snapshot(reader, root)


def test_row_size_and_time_budget_fail_closed(stream_case, monkeypatch):
    _, reader, root, _ = stream_case
    with pytest.raises(LocalQuotaStopped, match="BUDGET_EXCEEDED"):
        guard.preserved_snapshot(None, root, deadline=time.monotonic() - 1)
    monkeypatch.setattr(guard, "MAX_ROW_BYTES", 1)
    with pytest.raises(LocalQuotaStopped, match="ROW_SIZE_LIMIT"):
        guard.preserved_snapshot(reader, root)


def test_row_count_ceiling_refuses_instead_of_returning_partial_digest(stream_case, monkeypatch):
    admin, reader, root, _ = stream_case
    with admin.begin() as c:
        c.execute(text("CREATE TABLE demo_count_limit(id integer PRIMARY KEY)"))
        c.execute(text("INSERT INTO demo_count_limit VALUES (1),(2),(3)"))
    monkeypatch.setattr(guard, "MAX_TABLE_ROWS", 2)
    with pytest.raises(LocalQuotaStopped, match="DATABASE_LIMIT"):
        guard.preserved_snapshot(reader, root)


def test_old_bulk_query_times_out_while_keyset_reads_all_rows_at_same_timeout(stream_case):
    admin, _, root, schema = stream_case
    role = "read_stream_" + uuid4().hex[:12]
    reader = None
    try:
        with admin.begin() as c:
            c.execute(text("CREATE ROLE " + role))
            c.execute(text("CREATE TABLE demo_slow_history(id integer PRIMARY KEY, value text)"))
            c.execute(
                text(
                    "INSERT INTO demo_slow_history SELECT n,'unchanged' FROM generate_series(1,80) n"
                )
            )
            c.execute(
                text(
                    "CREATE FUNCTION slow_allow(integer) RETURNS boolean LANGUAGE plpgsql VOLATILE AS $$ BEGIN PERFORM pg_sleep(0.01); RETURN true; END $$"
                )
            )
            c.execute(text("ALTER TABLE demo_slow_history ENABLE ROW LEVEL SECURITY"))
            c.execute(
                text("CREATE POLICY synthetic_latency ON demo_slow_history USING (slow_allow(id))")
            )
            c.execute(text('GRANT USAGE ON SCHEMA "' + schema + '" TO ' + role))
            c.execute(text('GRANT SELECT ON ALL TABLES IN SCHEMA "' + schema + '" TO ' + role))
        reader = create_engine(
            os.environ["TEST_DATABASE_URL"],
            connect_args={
                "options": f"-csearch_path={schema} -crole={role} -cstatement_timeout=200"
            },
        )
        with pytest.raises(DBAPIError) as error:
            with reader.connect() as c, c.begin():
                c.execute(text("SELECT row_to_json(t) FROM demo_slow_history t LIMIT 10001")).all()
        assert error.value.orig.sqlstate == "57014"
        with reader.connect() as c, c.begin():
            c.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            table = guard._table_snapshot(c, inspect(c), "demo_slow_history", time.monotonic() + 60)
            assert c.execute(text("SHOW statement_timeout")).scalar_one() == "200ms"
        assert table["row_count"] == 80
        assert guard.preserved_snapshot(reader, root) == guard.preserved_snapshot(reader, root)
    finally:
        if reader is not None:
            reader.dispose()
        with admin.begin() as c:
            c.execute(text("DROP OWNED BY " + role))
            c.execute(text("DROP ROLE " + role))
