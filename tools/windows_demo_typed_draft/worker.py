"""Pinned in-memory typed review against fresh-copy read-only SQL over a local socket."""

import hashlib
import json
import os
import re
import sys
import types
from pathlib import Path


def collect(config, module_body, sql_body):
    stage = "SOURCE_CHECK"
    try:
        os.environ.clear()
        sys.path.insert(0, "/app")
        if (
            set(config)
            != {"baseline_files", "candidate_sha256", "sql_sha256", "params", "expected"}
            or len(config["baseline_files"]) != 89
        ):
            raise ValueError("MODEL_WORKER_CONFIG_INVALID")
        for name, digest in config["baseline_files"].items():
            if (
                not isinstance(name, str)
                or name.startswith("/")
                or ".." in name
                or not name.endswith(".py")
            ):
                raise ValueError("MODEL_BASELINE_INVALID")
            body = (Path("/app") / name).read_bytes()
            if len(body) > 2 * 1024**2 or hashlib.sha256(body).hexdigest() != digest:
                raise ValueError("MODEL_BASELINE_MISMATCH")
        if (
            hashlib.sha256(module_body).hexdigest() != config["candidate_sha256"]
            or hashlib.sha256(sql_body).hexdigest() != config["sql_sha256"]
        ):
            raise ValueError("MODEL_SOURCE_MISMATCH")
        sql = sql_body.decode("utf-8")
        if sql.count("WITH original AS (") != 1 or sql.count("\nSELECT json_build_object(") != 1:
            raise ValueError("MODEL_SQL_SOURCE_INVALID")
        query = (
            "WITH original AS ("
            + sql.split("WITH original AS (", 1)[1].split("\nSELECT json_build_object(", 1)[0]
        )
        for key in ("draft_day", "draft_week", "draft_amount", "draft_evidence"):
            query = query.replace(":'" + key + "'", "%(" + key + ")s::text")
        query += "\nSELECT original.state,candidate.after_state,original.state::text,candidate.after_state::text,transition.report FROM original CROSS JOIN candidate CROSS JOIN transition"
        stage = "MODEL_IMPORT"
        name = "services.execution.demo_period_draft_model_review"
        module = types.ModuleType(name)
        module.__package__ = "services.execution"
        sys.modules[name] = module
        exec(compile(module_body, "<typed-draft-review>", "exec"), module.__dict__)
        import psycopg

        rows = []
        stage = "SQL_CONNECT"
        with psycopg.connect(
            "dbname=hybrid user=dev host=/review-socket",
            connect_timeout=3,
            options="-c default_transaction_read_only=on -c statement_timeout=5000 -c timezone=UTC",
        ) as conn:
            with conn.cursor() as cursor:
                stage = "SQL_PRECHECK"
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                cursor.execute("SET LOCAL search_path=pg_catalog,public")
                cursor.execute("SET LOCAL lock_timeout='2s'")
                cursor.execute("SHOW transaction_read_only")
                if cursor.fetchone() != ("on",):
                    raise ValueError("MODEL_READ_ONLY_REQUIRED")
                cursor.execute(
                    "SELECT octet_length(state::text) FROM demo_accounts WHERE account_id='DEMO-WINDOWS-ISOLATED-BTC-V1'"
                )
                size = cursor.fetchone()
                if size is None or not 0 < size[0] <= 2 * 1024**2:
                    raise ValueError("MODEL_STATE_SIZE_LIMIT")
                for _ in range(2):
                    stage = "SQL_READ"
                    cursor.execute(query, config["params"])
                    data = cursor.fetchall()
                    if len(data) != 1:
                        raise ValueError("MODEL_ACCOUNT_NOT_UNIQUE")
                    before, after, before_text, after_text, report = data[0]
                    if (
                        hashlib.sha256(before_text.encode()).hexdigest()
                        != report["before_state_sha256"]
                        or hashlib.sha256(after_text.encode()).hexdigest()
                        != report["candidate_draft_state_sha256"]
                    ):
                        raise ValueError("MODEL_SQL_DIGEST_MISMATCH")
                    stage = "MODEL_VALIDATE"
                    rows.append(module.review_models(before, after, report, config["expected"]))
        if rows[0] != rows[1]:
            raise ValueError("MODEL_REPEATED_READ_CHANGED")
        return rows[0] | dict(
            copy_sql_read_only=True, repeated_read_equal=True, baseline_files_verified=89
        ), 0
    except Exception as error:
        reason = "MODEL_WORKER_BLOCKED"
        if type(error) is ValueError or any(
            c.__name__ == "DraftModelBlocked" for c in type(error).__mro__
        ):
            if re.fullmatch(r"MODEL_[A-Z0-9_]{1,100}", str(error)):
                reason = str(error)
        return dict(
            typed_draft_review="BLOCKED_NO_APPLY",
            failure_category=reason,
            failure_stage=stage,
            exception_type=type(error).__name__
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", type(error).__name__)
            else "OTHER",
            database_changed=False,
            baseline_adopted=False,
            orders_enabled=False,
            live_trading_enabled=False,
        ), 2


if __name__ == "__main__":
    value, status = collect(
        globals().get("CONFIG"), globals().get("MODULE_BODY", b""), globals().get("SQL_BODY", b"")
    )
    print(json.dumps(value, sort_keys=True))
    raise SystemExit(status)
