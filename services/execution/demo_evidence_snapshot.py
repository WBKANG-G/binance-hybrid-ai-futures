"""Bounded, read-only review fingerprints, not a backup or deployment authority.

Every ordinary persistent table in current_schema is included, irrespective of
name prefix. Unsupported relation kinds fail closed. PG and filesystem capture
are separate observations: stopped writers must be independently established.
"""

import hashlib
import time
from pathlib import Path
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection, Engine

from packages.persistence.demo_order_quota import canonical
from packages.persistence.demo_read_audit import require
from packages.persistence.demo_read_recovery import _file, _private_directory

POLICY = "FULL_SCHEMA_GET_EVIDENCE_REVIEW_SNAPSHOT_V1"
MAX_ROWS = 1_000_000
MAX_BYTES = 128 * 1024 * 1024
MAX_ROW_BYTES = 16 * 1024 * 1024


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def budget(deadline: float) -> None:
    require(time.monotonic() <= deadline, "EVIDENCE_SNAPSHOT_TIME_LIMIT")


def table_rows(c: Connection, name: str, deadline: float) -> dict[str, Any]:
    inspector = inspect(c)
    schema = c.execute(text("SELECT current_schema()")).scalar_one()
    keys = inspector.get_pk_constraint(name, schema=schema).get("constrained_columns")
    require(
        isinstance(keys, list) and 1 <= len(keys) <= 8, "EVIDENCE_SNAPSHOT_PRIMARY_KEY_REQUIRED"
    )
    quote = c.dialect.identifier_preparer.quote_identifier
    order = ",".join("s." + quote(k) for k in keys)
    projection = ",".join("t." + quote(k) for k in keys)
    placeholders = ",".join(":k" + str(i) for i in range(len(keys)))
    last = None
    h = hashlib.sha256()
    count = size = 0
    while True:
        budget(deadline)
        where = "" if last is None else f"WHERE ROW({order}) > ROW({placeholders})"
        query = text(
            f"SELECT octet_length(row_to_json(t)::text),CASE WHEN "
            f"octet_length(row_to_json(t)::text)<=:row_limit THEN row_to_json(t)::text END,{projection} "
            f"FROM (SELECT * FROM {quote(schema)}.{quote(name)} s {where} ORDER BY {order} LIMIT 1) t"
        )
        row = c.execute(
            query,
            dict(
                row_limit=MAX_ROW_BYTES,
                **({} if last is None else {f"k{i}": v for i, v in enumerate(last)}),
            ),
        ).first()
        budget(deadline)
        if row is None:
            break
        require(row[0] <= MAX_ROW_BYTES, "EVIDENCE_SNAPSHOT_ROW_LIMIT")
        body = row[1].encode()
        key = tuple(row[2:])
        require(all(v is not None for v in key) and key != last, "EVIDENCE_SNAPSHOT_KEY_INVALID")
        size += len(body)
        count += 1
        require(count <= MAX_ROWS and size <= MAX_BYTES, "EVIDENCE_SNAPSHOT_DATA_LIMIT")
        h.update(len(body).to_bytes(8, "big"))
        h.update(body)
        last = key
    return dict(rows=count, bytes=size, sha256=h.hexdigest())


CATALOG = {
    "namespace": "SELECT nspname,pg_get_userbyid(nspowner),nspacl FROM pg_namespace WHERE nspname=current_schema()",
    "enums": """SELECT t.typname,e.enumsortorder,e.enumlabel FROM pg_type t
       JOIN pg_namespace n ON n.oid=t.typnamespace JOIN pg_enum e ON e.enumtypid=t.oid
       WHERE n.nspname=current_schema() ORDER BY t.typname,e.enumsortorder""",
    "relations": """SELECT r.relname,r.relkind,r.relpersistence,r.relrowsecurity,r.relforcerowsecurity,
       r.relreplident,r.reloptions,pg_get_userbyid(r.relowner),r.relacl
       FROM pg_class r JOIN pg_namespace n ON n.oid=r.relnamespace
       WHERE n.nspname=current_schema() AND r.relkind NOT IN ('i','I','t') ORDER BY r.relname""",
    "columns": """SELECT r.relname,a.attnum,a.attname,format_type(a.atttypid,a.atttypmod),
       a.attnotnull,a.attidentity,a.attgenerated,a.attisdropped,a.attcollation::regcollation::text,
       pg_get_expr(d.adbin,d.adrelid),a.attacl
       FROM pg_attribute a JOIN pg_class r ON r.oid=a.attrelid JOIN pg_namespace n ON n.oid=r.relnamespace
       LEFT JOIN pg_attrdef d ON d.adrelid=r.oid AND d.adnum=a.attnum
       WHERE n.nspname=current_schema() AND a.attnum>0 ORDER BY r.relname,a.attnum""",
    "constraints": """SELECT r.relname,k.conname,k.contype,k.convalidated,k.condeferrable,k.condeferred,
       pg_get_constraintdef(k.oid,true) FROM pg_constraint k JOIN pg_class r ON r.oid=k.conrelid
       JOIN pg_namespace n ON n.oid=r.relnamespace WHERE n.nspname=current_schema()
       ORDER BY r.relname,k.conname""",
    "indexes": """SELECT r.relname,x.relname,i.indisvalid,i.indisready,i.indislive,
       pg_get_indexdef(i.indexrelid) FROM pg_index i JOIN pg_class r ON r.oid=i.indrelid
       JOIN pg_class x ON x.oid=i.indexrelid JOIN pg_namespace n ON n.oid=r.relnamespace
       WHERE n.nspname=current_schema() ORDER BY r.relname,x.relname""",
    "rules": """SELECT r.relname,w.rulename,pg_get_ruledef(w.oid,true)
       FROM pg_rewrite w JOIN pg_class r ON r.oid=w.ev_class
       JOIN pg_namespace n ON n.oid=r.relnamespace
       WHERE n.nspname=current_schema() ORDER BY r.relname,w.rulename""",
    "triggers": """SELECT r.relname,t.tgname,t.tgenabled,pg_get_triggerdef(t.oid,true)
       FROM pg_trigger t JOIN pg_class r ON r.oid=t.tgrelid JOIN pg_namespace n ON n.oid=r.relnamespace
       WHERE n.nspname=current_schema() AND NOT t.tgisinternal ORDER BY r.relname,t.tgname""",
    "functions": """SELECT p.proname,pg_get_function_identity_arguments(p.oid),pg_get_functiondef(p.oid),
       pg_get_userbyid(p.proowner),p.proacl FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
       WHERE n.nspname=current_schema() AND p.prokind IN ('f','p') ORDER BY p.proname,2""",
    "policies": """SELECT tablename,policyname,permissive,roles,cmd,qual,with_check FROM pg_policies
       WHERE schemaname=current_schema() ORDER BY tablename,policyname""",
}


def evidence_snapshot(engine: Engine, storage: Path, *, seconds: float = 60.0) -> dict[str, Any]:
    require(engine.dialect.name == "postgresql", "EVIDENCE_SNAPSHOT_POSTGRES_REQUIRED")
    require(type(seconds) in (float, int) and 0 < seconds <= 60, "EVIDENCE_SNAPSHOT_BUDGET_INVALID")
    deadline = time.monotonic() + seconds
    catalog: dict[str, Any] = {}
    tables = {}
    with engine.connect() as c, c.begin():
        c.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        c.execute(text("SET LOCAL statement_timeout='2s'"))
        c.execute(text("SET LOCAL lock_timeout='2s'"))
        relations = c.execute(text(CATALOG["relations"] + " LIMIT 101")).all()
        require(
            1 <= len(relations) <= 100
            and all(r[1] == "r" and r[2] == "p" and not r[3] and not r[4] for r in relations),
            "EVIDENCE_SNAPSHOT_UNSUPPORTED_RELATIONS",
        )
        unsupported = c.execute(
            text("""SELECT
            (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
             WHERE n.nspname=current_schema() AND p.prokind NOT IN ('f','p')) +
            (SELECT count(*) FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
             WHERE n.nspname=current_schema() AND t.typelem=0 AND
             t.typtype<>'e' AND NOT (t.typtype='c' AND t.typrelid<>0))""")
        ).scalar_one()
        require(unsupported == 0, "EVIDENCE_SNAPSHOT_UNSUPPORTED_TYPES_OR_ROUTINES")
        inherited = c.execute(
            text("""SELECT count(*) FROM pg_inherits i
            JOIN pg_class a ON a.oid=i.inhrelid JOIN pg_namespace an ON an.oid=a.relnamespace
            JOIN pg_class b ON b.oid=i.inhparent JOIN pg_namespace bn ON bn.oid=b.relnamespace
            WHERE an.nspname=current_schema() OR bn.nspname=current_schema()""")
        ).scalar_one()
        require(inherited == 0, "EVIDENCE_SNAPSHOT_UNSUPPORTED_INHERITANCE")
        for name, query in CATALOG.items():
            budget(deadline)
            rows = [list(r) for r in c.execute(text(query + " LIMIT 4097")).all()]
            require(
                len(rows) <= 4096 and len(canonical(rows).encode()) <= 4 * 1024 * 1024,
                "EVIDENCE_SNAPSHOT_CATALOG_LIMIT",
            )
            # Only hashes are returned; function bodies, account values and rows stay private.
            key_columns = 1 if name in {"namespace", "relations"} else 2
            objects = {canonical(row[:key_columns]): digest(row) for row in rows}
            require(len(objects) == len(rows), "EVIDENCE_SNAPSHOT_CATALOG_KEY_COLLISION")
            catalog[name] = dict(count=len(rows), sha256=digest(rows), objects=objects)
        for relation in relations:
            tables[relation[0]] = table_rows(c, relation[0], deadline)
        require(
            sum(t["bytes"] for t in tables.values()) <= MAX_BYTES, "EVIDENCE_SNAPSHOT_DATA_LIMIT"
        )
    _private_directory(storage)
    entries: dict[str, str] = {}
    size = 0
    for path in sorted(storage.rglob("*")):
        budget(deadline)
        require(not path.is_symlink(), "EVIDENCE_SNAPSHOT_SYMLINK")
        if path.is_dir():
            _private_directory(path)
            continue
        require(
            not path.name.endswith(("-wal", "-shm", "-journal")), "EVIDENCE_SNAPSHOT_STORAGE_BUSY"
        )
        body = _file(path)
        size += len(body)
        require(len(entries) < 128 and size <= MAX_BYTES, "EVIDENCE_SNAPSHOT_STORAGE_LIMIT")
        entries[str(path.relative_to(storage))] = hashlib.sha256(body).hexdigest()
    require(bool(entries), "EVIDENCE_SNAPSHOT_STORAGE_MISSING")
    budget(deadline)
    value = dict(policy=POLICY, tables=tables, catalog=catalog, files=entries)
    return dict(
        **value,
        sha256=digest(value),
        recoverable_backup=False,
        writers_stopped_verified=False,
        cross_store_atomic=False,
        execution_approved=False,
        orders_enabled=False,
        live_trading_enabled=False,
    )
