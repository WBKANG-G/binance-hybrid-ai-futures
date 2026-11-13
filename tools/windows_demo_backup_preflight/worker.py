"""Streamed backup-scope observation; no application imports or file-body reads."""

import hashlib
import json
import os
import re
import stat
import time
from pathlib import Path
from typing import Any

ACCOUNT = "DEMO-WINDOWS-ISOLATED-BTC-V1"
STORAGE = "/app/demo-export-state"
MAX_ENTRIES = 1024
MAX_TABLES = 128
KNOWN_TABLES = (
    "demo_accounts",
    "demo_events",
    "demo_outbox",
    "demo_read_audit_bindings",
    "demo_read_audit_events",
    "demo_shared_weight_installation",
    "demo_shared_weight_bindings",
    "demo_shared_weight_checkpoints",
    "demo_urgent_alerts",
    "demo_urgent_alert_attempts",
    "read_response_evidence_bindings_v2",
    "read_response_evidence_events_v2",
    "wire_checkpoint_installation_v2",
    "wire_checkpoint_bindings_v2",
    "wire_checkpoint_events_v2",
    "paper_accounts",
    "paper_venues",
    "paper_events",
    "paper_outbox",
    "market_raw_events",
    "market_candles",
    "market_books",
    "market_stream_status",
)
FALSE_FLAGS = (
    "database_changed",
    "files_changed",
    "backup_created",
    "restore_tested",
    "api_replaced",
    "writers_stopped",
    "writer_quiescence_verified",
    "exchange_credentials_accessed",
    "credential_session_opened",
    "journal_opened",
    "clock_recovery_attempted",
    "offline_copy_performed",
    "reference_generated",
    "automatic_retry",
    "backup_ready",
    "installation_ready",
    "execution_approved",
    "orders_enabled",
    "live_trading_enabled",
)


def require(ok: bool, code: str = "PREFLIGHT_INPUT_INVALID") -> None:
    if not ok:
        raise ValueError(code)


def base_report() -> dict[str, Any]:
    return dict(
        backup_scope_preflight="BLOCKED_KEEP_PAUSED",
        stage="CONFIGURATION",
        failure_category="PREFLIGHT_INPUT_INVALID",
        sqlstate=None,
        source_reference="",
        metadata_only=True,
        actual_http_attempts=0,
        order_writes=0,
        account_found=False,
        account_paused=None,
        active_lease_observed=None,
        database_configuration_used=False,
        database_scope=None,
        tables=[],
        catalog=None,
        storage=None,
        observation_atomic=False,
        file_contents_verified=False,
        table_contents_verified=False,
        schema_definitions_verified=False,
        next_action="REVIEW_SCOPE_NO_AUTOMATIC_BACKUP_INSTALL_OR_RETRY",
        **{flag: False for flag in FALSE_FLAGS},
    )


def check_time(deadline: float) -> None:
    require(time.monotonic() <= deadline, "PREFLIGHT_TIME_LIMIT")


def storage_metadata(root: Path, deadline: float) -> dict[str, Any]:
    """Descriptor-relative traversal, no links or bodies, bounded metadata only."""
    totals = dict(
        entries=0,
        files=0,
        directories=0,
        logical_bytes=0,
        sqlite_wal_files=0,
        sqlite_shm_files=0,
        sqlite_journal_files=0,
        group_or_other_writable_entries=0,
    )
    groups = {
        name: dict(files=0, logical_bytes=0) for name in ("SHARED_WEIGHT", "ORDER_QUOTA", "OTHER")
    }
    fingerprint = hashlib.sha256()
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    # Refuse a symlink in any root component, including /app and the mount root.
    fd = os.open("/", flags)
    try:
        for part in root.parts[1:]:
            check_time(deadline)
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child

        def visit(directory: int, parts: tuple[str, ...], depth: int) -> None:
            require(depth <= 12, "PREFLIGHT_STORAGE_DEPTH_LIMIT")
            check_time(deadline)
            before = os.fstat(directory)
            require(stat.S_ISDIR(before.st_mode), "PREFLIGHT_STORAGE_UNSAFE")
            with os.scandir(directory) as entries:
                names: list[str] = []
                for entry in entries:
                    check_time(deadline)
                    require(len(names) < MAX_ENTRIES, "PREFLIGHT_STORAGE_ENTRY_LIMIT")
                    names.append(entry.name)
            for name in sorted(names):
                check_time(deadline)
                totals["entries"] += 1
                require(totals["entries"] <= MAX_ENTRIES, "PREFLIGHT_STORAGE_ENTRY_LIMIT")
                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                relative = parts + (name,)
                require(not stat.S_ISLNK(info.st_mode), "PREFLIGHT_STORAGE_UNSAFE")
                require(
                    stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode),
                    "PREFLIGHT_STORAGE_UNSAFE",
                )
                require(
                    not stat.S_ISREG(info.st_mode) or info.st_nlink == 1, "PREFLIGHT_STORAGE_UNSAFE"
                )
                totals["group_or_other_writable_entries"] += bool(info.st_mode & 0o022)
                fingerprint.update(
                    json.dumps(
                        [
                            relative,
                            info.st_mode,
                            info.st_size,
                            info.st_mtime_ns,
                            info.st_dev,
                            info.st_ino,
                        ],
                        ensure_ascii=True,
                    ).encode()
                )
                if stat.S_ISDIR(info.st_mode):
                    totals["directories"] += 1
                    child = os.open(name, flags, dir_fd=directory)
                    try:
                        opened = os.fstat(child)
                        require(
                            (opened.st_dev, opened.st_ino) == (info.st_dev, info.st_ino),
                            "PREFLIGHT_STORAGE_CHANGED",
                        )
                        visit(child, relative, depth + 1)
                    finally:
                        os.close(child)
                else:
                    totals["files"] += 1
                    totals["logical_bytes"] += info.st_size
                    kind = "OTHER"
                    if relative[:2] == ("demo-runtime-20261111", "shared-weight"):
                        kind = "SHARED_WEIGHT"
                    elif relative[:2] == ("demo-runtime-20261111", "order-quota"):
                        kind = "ORDER_QUOTA"
                    groups[kind]["files"] += 1
                    groups[kind]["logical_bytes"] += info.st_size
                    totals["sqlite_wal_files"] += name.endswith("-wal")
                    totals["sqlite_shm_files"] += name.endswith("-shm")
                    totals["sqlite_journal_files"] += name.endswith("-journal")
                after = os.stat(name, dir_fd=directory, follow_symlinks=False)
                require(
                    (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns)
                    == (
                        after.st_dev,
                        after.st_ino,
                        after.st_mode,
                        after.st_size,
                        after.st_mtime_ns,
                    ),
                    "PREFLIGHT_STORAGE_CHANGED",
                )
            after_directory = os.fstat(directory)
            require(
                (before.st_mtime_ns, before.st_ctime_ns)
                == (after_directory.st_mtime_ns, after_directory.st_ctime_ns),
                "PREFLIGHT_STORAGE_CHANGED",
            )

        visit(fd, (), 0)
    finally:
        os.close(fd)
    return dict(root=STORAGE, **totals, groups=groups, metadata_sha256=fingerprint.hexdigest())


def collect(config: Any, *, storage_root: Path = Path(STORAGE)) -> tuple[dict[str, Any], int]:
    report = base_report()
    engine = None
    deadline = time.monotonic() + 15
    try:
        require(
            isinstance(config, dict)
            and set(config) == {"source_reference", "account", "storage_root"}
        )
        require(
            isinstance(config["source_reference"], str)
            and re.fullmatch("[0-9a-f]{40}", config["source_reference"]) is not None
        )
        require(config["account"] == ACCOUNT and config["storage_root"] == STORAGE)
        report["source_reference"] = config["source_reference"]
        report["stage"] = "DATABASE_CONFIGURATION"
        url = os.environ.get("DATABASE_URL")
        require(bool(url), "PREFLIGHT_DATABASE_NOT_CONFIGURED")
        assert url is not None
        report["database_configuration_used"] = True
        from sqlalchemy import create_engine, text
        from sqlalchemy.engine import make_url

        parsed = make_url(url)
        require(parsed.drivername in {"postgresql", "postgresql+psycopg"})
        engine = create_engine(
            parsed.set(drivername="postgresql+psycopg"),
            echo=False,
            hide_parameters=True,
            pool_size=1,
            max_overflow=0,
            pool_timeout=3,
            connect_args={"connect_timeout": 3},
        )
        report["stage"] = "DATABASE_CATALOG"
        with engine.connect().execution_options(isolation_level="REPEATABLE READ") as c, c.begin():
            c.execute(text("SET TRANSACTION READ ONLY"))
            c.execute(text("SET LOCAL statement_timeout='2s'"))
            c.execute(text("SET LOCAL lock_timeout='2s'"))
            check_time(deadline)
            scope = c.execute(
                text(
                    "SELECT encode(sha256(convert_to(current_database() || '/' || current_schema(),'UTF8')),'hex')"
                )
            ).scalar_one()
            report["database_scope"] = scope
            # No application-table contents, function/trigger bodies, names or URLs returned.
            rows = c.execute(
                text("""SELECT
              CASE WHEN r.relname=ANY(CAST(:known AS text[])) THEN r.relname ELSE 'UNCLASSIFIED' END AS label,
              encode(sha256(convert_to(n.nspname || '/' || r.relname,'UTF8')),'hex') AS identity,
              r.relkind,r.relpersistence,
              CASE WHEN r.relkind IN ('r','p','m','S') THEN pg_total_relation_size(r.oid) ELSE 0 END AS bytes
              FROM pg_class r JOIN pg_namespace n ON n.oid=r.relnamespace
              WHERE n.nspname=current_schema() AND r.relkind IN ('r','p','m','v','f','S')
              ORDER BY r.oid LIMIT 129"""),
                dict(known=list(KNOWN_TABLES)),
            ).all()
            require(len(rows) <= MAX_TABLES, "PREFLIGHT_DATABASE_OBJECT_LIMIT")
            report["tables"] = [
                dict(
                    name=label,
                    identity_sha256=identity,
                    kind=kind,
                    persistence=persistence,
                    total_bytes=size,
                )
                for label, identity, kind, persistence, size in rows
            ]
            check_time(deadline)
            counts = (
                c.execute(
                    text("""SELECT
              (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=current_schema()) AS functions,
              (SELECT count(*) FROM pg_trigger t JOIN pg_class r ON r.oid=t.tgrelid JOIN pg_namespace n ON n.oid=r.relnamespace WHERE n.nspname=current_schema() AND NOT t.tgisinternal) AS triggers,
              (SELECT count(*) FROM pg_class r JOIN pg_namespace n ON n.oid=r.relnamespace WHERE n.nspname=current_schema() AND r.relkind IN ('i','I')) AS indexes,
              (SELECT count(*) FROM pg_constraint k JOIN pg_namespace n ON n.oid=k.connamespace WHERE n.nspname=current_schema()) AS constraints,
              (SELECT count(*) FROM pg_constraint k JOIN pg_class a ON a.oid=k.conrelid JOIN pg_class b ON b.oid=k.confrelid JOIN pg_namespace na ON na.oid=a.relnamespace JOIN pg_namespace nb ON nb.oid=b.relnamespace WHERE k.contype='f' AND (na.nspname=current_schema() OR nb.nspname=current_schema()) AND na.oid<>nb.oid) AS cross_schema_foreign_keys,
              (SELECT count(*) FROM pg_inherits i JOIN pg_class a ON a.oid=i.inhrelid JOIN pg_class b ON b.oid=i.inhparent JOIN pg_namespace na ON na.oid=a.relnamespace JOIN pg_namespace nb ON nb.oid=b.relnamespace WHERE (na.nspname=current_schema() OR nb.nspname=current_schema()) AND na.oid<>nb.oid) AS cross_schema_inheritance
              """)
                )
                .mappings()
                .one()
            )
            require(
                all(type(v) is int and 0 <= v <= 4096 for v in counts.values()),
                "PREFLIGHT_DATABASE_OBJECT_LIMIT",
            )
            report["catalog"] = dict(counts)
            require(
                any(r[0] == "demo_accounts" and r[2] == "r" for r in rows),
                "PREFLIGHT_ACCOUNT_TABLE_MISSING",
            )
            report["stage"] = "ACCOUNT_HEADER"
            row = (
                c.execute(
                    text("""SELECT jsonb_typeof(state->'paused') AS kind,
                state->>'paused' AS paused, lease_until > now() AS active
                FROM demo_accounts WHERE account_id=:account"""),
                    dict(account=ACCOUNT),
                )
                .mappings()
                .one_or_none()
            )
            require(row is not None, "PREFLIGHT_ACCOUNT_NOT_FOUND")
            assert row is not None
            report["account_found"] = True
            require(
                row["kind"] == "boolean" and row["paused"] in {"true", "false"},
                "PREFLIGHT_ACCOUNT_HEADER_INVALID",
            )
            report["account_paused"] = row["paused"] == "true"
            report["active_lease_observed"] = bool(row["active"])
            require(report["account_paused"], "PREFLIGHT_ACCOUNT_NOT_PAUSED")
        report["stage"] = "STORAGE_METADATA"
        report["storage"] = storage_metadata(storage_root, deadline)
        check_time(deadline)
        report.update(
            backup_scope_preflight="READ_ONLY_SCOPE_OBSERVED_KEEP_PAUSED",
            stage="COMPLETE",
            failure_category="NONE",
        )
        return report, 0
    except Exception as error:
        allowed = {
            "PREFLIGHT_INPUT_INVALID",
            "PREFLIGHT_TIME_LIMIT",
            "PREFLIGHT_STORAGE_DEPTH_LIMIT",
            "PREFLIGHT_STORAGE_ENTRY_LIMIT",
            "PREFLIGHT_STORAGE_UNSAFE",
            "PREFLIGHT_STORAGE_CHANGED",
            "PREFLIGHT_DATABASE_NOT_CONFIGURED",
            "PREFLIGHT_DATABASE_OBJECT_LIMIT",
            "PREFLIGHT_ACCOUNT_TABLE_MISSING",
            "PREFLIGHT_ACCOUNT_NOT_FOUND",
            "PREFLIGHT_ACCOUNT_HEADER_INVALID",
            "PREFLIGHT_ACCOUNT_NOT_PAUSED",
        }
        report["failure_category"] = (
            str(error)
            if type(error) is ValueError and str(error) in allowed
            else "PREFLIGHT_READ_FAILED"
        )
        sqlstate = getattr(getattr(error, "orig", None), "sqlstate", None)
        if isinstance(sqlstate, str) and re.fullmatch("[A-Z0-9]{5}", sqlstate):
            report["sqlstate"] = sqlstate
        return report, 2
    finally:
        if engine is not None:
            try:
                engine.dispose()
            except Exception:
                pass


if __name__ == "__main__":
    result, status = collect(globals().get("CONFIG"))
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(status)


def report_schema() -> dict[str, Any]:
    """Closed output grammar used by the host before displaying any worker values."""

    def obj(fields: dict[str, Any], nullable: bool = False) -> dict[str, Any]:
        return dict(kind="object", fields=fields, nullable=nullable)

    def string(*values: str, pattern: str | None = None, nullable: bool = False) -> dict[str, Any]:
        return dict(kind="string", values=list(values), pattern=pattern, nullable=nullable)

    def boolean(value: bool | None = None, nullable: bool = False) -> dict[str, Any]:
        return dict(kind="boolean", value=value, nullable=nullable)

    integer = dict(kind="integer", minimum=0, maximum=9007199254740991, nullable=False)
    hexdigest = string(pattern="^[0-9a-f]{64}$")
    fields: dict[str, Any] = {flag: boolean(False) for flag in FALSE_FLAGS}
    fields.update(
        {
            flag: boolean(False)
            for flag in (
                "observation_atomic",
                "file_contents_verified",
                "table_contents_verified",
                "schema_definitions_verified",
            )
        }
    )
    fields.update(
        backup_scope_preflight=string(
            "BLOCKED_KEEP_PAUSED", "READ_ONLY_SCOPE_OBSERVED_KEEP_PAUSED"
        ),
        stage=string(
            "CONFIGURATION",
            "DATABASE_CONFIGURATION",
            "DATABASE_CATALOG",
            "ACCOUNT_HEADER",
            "STORAGE_METADATA",
            "COMPLETE",
        ),
        failure_category=string(
            "NONE",
            "PREFLIGHT_INPUT_INVALID",
            "PREFLIGHT_TIME_LIMIT",
            "PREFLIGHT_STORAGE_DEPTH_LIMIT",
            "PREFLIGHT_STORAGE_ENTRY_LIMIT",
            "PREFLIGHT_STORAGE_UNSAFE",
            "PREFLIGHT_STORAGE_CHANGED",
            "PREFLIGHT_DATABASE_NOT_CONFIGURED",
            "PREFLIGHT_DATABASE_OBJECT_LIMIT",
            "PREFLIGHT_ACCOUNT_TABLE_MISSING",
            "PREFLIGHT_ACCOUNT_NOT_FOUND",
            "PREFLIGHT_ACCOUNT_HEADER_INVALID",
            "PREFLIGHT_ACCOUNT_NOT_PAUSED",
            "PREFLIGHT_READ_FAILED",
        ),
        sqlstate=string(pattern="^[A-Z0-9]{5}$", nullable=True),
        source_reference=string(pattern="^(?:[0-9a-f]{40})?$"),
        metadata_only=boolean(True),
        actual_http_attempts=dict(integer, maximum=0),
        order_writes=dict(integer, maximum=0),
        account_found=boolean(),
        account_paused=boolean(nullable=True),
        active_lease_observed=boolean(nullable=True),
        database_configuration_used=boolean(),
        database_scope=dict(hexdigest, nullable=True),
        tables=dict(
            kind="array",
            maximum=MAX_TABLES,
            nullable=False,
            item=obj(
                dict(
                    name=string(*KNOWN_TABLES, "UNCLASSIFIED"),
                    identity_sha256=hexdigest,
                    kind=string("r", "p", "m", "v", "f", "S"),
                    persistence=string("p", "u", "t"),
                    total_bytes=integer,
                )
            ),
        ),
        catalog=obj(
            {
                key: dict(integer, maximum=4096)
                for key in (
                    "functions",
                    "triggers",
                    "indexes",
                    "constraints",
                    "cross_schema_foreign_keys",
                    "cross_schema_inheritance",
                )
            },
            nullable=True,
        ),
        storage=obj(
            dict(
                root=string(STORAGE),
                metadata_sha256=hexdigest,
                **{
                    key: integer
                    for key in (
                        "entries",
                        "files",
                        "directories",
                        "logical_bytes",
                        "sqlite_wal_files",
                        "sqlite_shm_files",
                        "sqlite_journal_files",
                        "group_or_other_writable_entries",
                    )
                },
                groups=obj(
                    {
                        key: obj(dict(files=integer, logical_bytes=integer))
                        for key in ("SHARED_WEIGHT", "ORDER_QUOTA", "OTHER")
                    }
                ),
            ),
            nullable=True,
        ),
        next_action=string("REVIEW_SCOPE_NO_AUTOMATIC_BACKUP_INSTALL_OR_RETRY"),
    )
    return obj(fields)
