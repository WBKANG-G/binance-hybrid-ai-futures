"""Standalone streamed inventory: no project imports, exchange API, or disk writes."""

import hashlib
import json
import os
import re
import stat
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

TABLES = (
    "demo_accounts",
    "demo_events",
    "demo_outbox",
    "demo_read_audit_bindings",
    "demo_read_audit_events",
    "read_response_evidence_bindings_v2",
    "read_response_evidence_events_v2",
    "wire_checkpoint_installation_v2",
    "wire_checkpoint_bindings_v2",
    "wire_checkpoint_events_v2",
)
REQUEST_STATES = (
    "UNKNOWN",
    "SUBMITTING",
    "CANCEL_REQUESTED",
    "ACKNOWLEDGED",
    "NOT_SENT",
    "REJECTED",
    "NOT_FOUND_UNRESOLVED",
    "OTHER",
)
RISK_STATES = (
    "NORMAL",
    "COOLDOWN",
    "DAILY_LOCK",
    "WEEKLY_LOCK",
    "DRAWDOWN_LOCK",
    "UNHEALTHY",
    "EMERGENCY",
)
MAX_FILE_BYTES = 2097152


def require(value: bool) -> None:
    if not value:
        raise ValueError("INVENTORY_INPUT_OR_STATE_INVALID")


def config_valid(config: Any) -> None:
    require(
        isinstance(config, dict)
        and set(config)
        == {
            "source_reference",
            "baseline_reference",
            "account",
            "expected_day_kst",
            "baseline_files",
            "candidate_files",
        }
    )
    for field in ("source_reference", "baseline_reference"):
        require(
            isinstance(config[field], str)
            and re.fullmatch("[0-9a-f]{40}", config[field]) is not None
        )
    require(config["account"] == "DEMO-WINDOWS-ISOLATED-BTC-V1")
    require(
        isinstance(config["expected_day_kst"], str)
        and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", config["expected_day_kst"]) is not None
    )
    for group in ("baseline_files", "candidate_files"):
        values = config[group]
        require(isinstance(values, dict) and 1 <= len(values) <= 128)
        for path, value in values.items():
            require(
                isinstance(path, str)
                and re.fullmatch(
                    r"(?:packages|services|scripts|integrations|apps|migrations)/[A-Za-z0-9_/]+\.py",
                    path,
                )
                is not None
                and ".." not in path
                and isinstance(value, str)
                and re.fullmatch("[0-9a-f]{64}", value) is not None
            )


def digest_file(root: Path, relative: str) -> str | None:
    current = root
    for part in Path(relative).parts[:-1]:
        current = current / part
        try:
            parent_info = current.lstat()
        except FileNotFoundError:
            return None
        require(stat.S_ISDIR(parent_info.st_mode))
    path = root / relative
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= MAX_FILE_BYTES)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        opened = os.fstat(fd)
        require((info.st_dev, info.st_ino) == (opened.st_dev, opened.st_ino))
        with os.fdopen(fd, "rb", closefd=False) as stream:
            body = stream.read(MAX_FILE_BYTES + 1)
        final = os.fstat(fd)
        require(
            len(body) <= MAX_FILE_BYTES
            and len(body) == info.st_size
            and (opened.st_size, opened.st_mtime_ns) == (final.st_size, final.st_mtime_ns)
            and (path.lstat().st_dev, path.lstat().st_ino) == (info.st_dev, info.st_ino)
        )
        return hashlib.sha256(body).hexdigest()
    finally:
        os.close(fd)


def base_report() -> dict[str, Any]:
    return dict(
        runtime_inventory="BLOCKED_KEEP_PAUSED",
        stage="CONFIGURATION",
        failure_category="INPUT_OR_STATE_INVALID",
        sqlstate=None,
        source_reference="",
        baseline_reference="",
        baseline_files_total=0,
        baseline_files_verified=0,
        candidate_files_total=0,
        candidate_files_matching=0,
        changed_or_missing_baseline_files=[],
        changed_or_missing_candidate_files=[],
        table_presence={name: False for name in TABLES},
        account_found=False,
        paused=None,
        risk_state=None,
        stored_day_kst=None,
        stored_week_kst=None,
        current_day_kst=None,
        expected_day_kst=None,
        period_keys_match=None,
        local_orders=0,
        request_state_counts={name: 0 for name in REQUEST_STATES},
        database_configuration_used=False,
        candidate_table_names_present=False,
        metadata_only=True,
        database_changed=False,
        source_files_changed=False,
        api_replaced=False,
        exchange_credentials_accessed=False,
        credential_session_opened=False,
        shared_journal_opened=False,
        clock_recovery_attempted=False,
        reference_generated=False,
        offline_copy_performed=False,
        automatic_retry=False,
        actual_http_attempts=0,
        order_writes=0,
        execution_approved=False,
        orders_enabled=False,
        live_trading_enabled=False,
    )


def collect(
    config: Any, *, root: Path = Path("/app"), now: datetime | None = None
) -> tuple[dict[str, Any], int]:
    report = base_report()
    engine = None
    try:
        config_valid(config)
        report.update(
            source_reference=config["source_reference"],
            baseline_reference=config["baseline_reference"],
            expected_day_kst=config["expected_day_kst"],
        )
        observed = now or datetime.now(UTC)
        require(observed.tzinfo is not None)
        day = observed.astimezone(timezone(timedelta(hours=9))).date()
        week = day - timedelta(days=day.weekday())
        report["current_day_kst"] = day.isoformat()
        report["stage"] = "SOURCE_FILES"
        require(stat.S_ISDIR(root.lstat().st_mode))
        actual = {
            name: digest_file(root, name)
            for name in sorted(config["baseline_files"].keys() | config["candidate_files"].keys())
        }
        for group, total_field, ok_field, differences in (
            (
                "baseline_files",
                "baseline_files_total",
                "baseline_files_verified",
                "changed_or_missing_baseline_files",
            ),
            (
                "candidate_files",
                "candidate_files_total",
                "candidate_files_matching",
                "changed_or_missing_candidate_files",
            ),
        ):
            report[total_field] = len(config[group])
            report[differences] = [
                name for name, expected in sorted(config[group].items()) if actual[name] != expected
            ]
            report[ok_field] = len(config[group]) - len(report[differences])
        report["stage"] = "DATABASE_CONFIGURATION"
        url = os.environ.get("DATABASE_URL")
        if not url:
            report["failure_category"] = "DATABASE_NOT_CONFIGURED"
            return report, 2
        report["database_configuration_used"] = True
        from sqlalchemy import create_engine, text
        from sqlalchemy.engine import make_url

        parsed_url = make_url(url)
        require(parsed_url.drivername in {"postgresql", "postgresql+psycopg"})
        engine = create_engine(
            parsed_url.set(drivername="postgresql+psycopg"),
            hide_parameters=True,
            echo=False,
            pool_size=1,
            max_overflow=0,
            pool_timeout=3,
            connect_args={"connect_timeout": 3},
        )
        report["stage"] = "DATABASE_SCHEMA"
        with engine.connect().execution_options(isolation_level="REPEATABLE READ") as c, c.begin():
            c.execute(text("SET TRANSACTION READ ONLY"))
            c.execute(text("SET LOCAL statement_timeout='2s'"))
            c.execute(text("SET LOCAL lock_timeout='2s'"))
            presence = c.execute(
                text(
                    "SELECT name,to_regclass(name) IS NOT NULL AS present FROM unnest(CAST(:names AS text[])) AS name"
                ),
                dict(names=list(TABLES)),
            ).all()
            report["table_presence"] = {name: bool(present) for name, present in presence}
            report["candidate_table_names_present"] = all(
                report["table_presence"][name] for name in TABLES[5:]
            )
            if not all(report["table_presence"][name] for name in TABLES[:3]):
                report["failure_category"] = "FINANCIAL_TABLES_MISSING"
                return report, 2
            report["stage"] = "ACCOUNT_PROJECTION"
            row = (
                c.execute(
                    text("""SELECT state->>'paused' AS paused, state->>'risk_state' AS risk,
                state->>'daily_key' AS day, state->>'weekly_key' AS week,
                jsonb_typeof(state->'orders') AS orders_type,jsonb_typeof(state->'requests') AS requests_type,
                jsonb_typeof(state->'paused') AS paused_type
                FROM demo_accounts WHERE account_id=:account"""),
                    dict(account=config["account"]),
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                report["failure_category"] = "ACCOUNT_NOT_FOUND"
                return report, 2
            report["account_found"] = True
            require(
                row["paused_type"] == "boolean"
                and row["paused"] in {"true", "false"}
                and row["risk"] in RISK_STATES
                and all(
                    isinstance(row[k], str)
                    and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", row[k]) is not None
                    for k in ("day", "week")
                )
                and row["orders_type"] == row["requests_type"] == "object"
            )
            orders, requests = c.execute(
                text("""SELECT
                (SELECT count(*) FROM jsonb_object_keys(state->'orders')) AS orders,
                (SELECT count(*) FROM jsonb_each_text(state->'requests')) AS requests
                FROM demo_accounts WHERE account_id=:account"""),
                dict(account=config["account"]),
            ).one()
            require(0 <= orders <= 128 and 0 <= requests <= 768)
            states = c.execute(
                text("""SELECT CASE WHEN value = ANY(CAST(:known AS text[])) THEN value ELSE 'OTHER' END AS category,count(*)
                FROM demo_accounts CROSS JOIN LATERAL jsonb_each_text(state->'requests')
                WHERE account_id=:account GROUP BY category"""),
                dict(account=config["account"], known=list(REQUEST_STATES[:-1])),
            ).all()
            counts = {key: 0 for key in REQUEST_STATES}
            for key, count in states:
                require(key in counts and type(count) is int and 0 <= count <= 768)
                counts[key] = count
            require(sum(counts.values()) == requests)
            report.update(
                account_found=True,
                paused=row["paused"] == "true",
                risk_state=row["risk"],
                stored_day_kst=row["day"],
                stored_week_kst=row["week"],
                period_keys_match=row["day"] == day.isoformat() and row["week"] == week.isoformat(),
                local_orders=orders,
                request_state_counts=counts,
            )
        report.update(
            runtime_inventory="READ_ONLY_INVENTORY_COMPLETE_KEEP_PAUSED",
            stage="COMPLETE",
            failure_category="NONE",
        )
        return report, 0
    except Exception as error:
        sqlstate = getattr(getattr(error, "orig", None), "sqlstate", None)
        if isinstance(sqlstate, str) and re.fullmatch("[A-Z0-9]{5}", sqlstate):
            report["sqlstate"] = sqlstate
        report["failure_category"] = (
            "SOURCE_OR_DATABASE_READ_FAILED"
            if report["stage"] != "CONFIGURATION"
            else "INPUT_OR_STATE_INVALID"
        )
        return report, 2
    finally:
        if engine is not None:
            try:
                engine.dispose()
            except Exception:
                pass


if __name__ == "__main__":
    # Runner defines CONFIG after verifying every input's transfer hash.
    result, status = collect(globals().get("CONFIG"))
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(status)
