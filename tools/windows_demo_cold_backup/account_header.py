"""Streamed read-only pause/lease check; no app imports, keys, periods or writes."""

import json
import os
from typing import Any


def collect() -> dict[str, Any]:
    result: dict[str, Any] = dict(
        status="BLOCKED",
        paused=False,
        active_lease=True,
        orders=0,
        unresolved_requests=0,
        database_changed=False,
    )
    engine = None
    try:
        from sqlalchemy import create_engine, text
        from sqlalchemy.engine import make_url

        url = make_url(os.environ["DATABASE_URL"])
        if url.drivername not in {"postgresql", "postgresql+psycopg"}:
            return result
        engine = create_engine(
            url.set(drivername="postgresql+psycopg"),
            echo=False,
            hide_parameters=True,
            connect_args={"connect_timeout": 3},
            pool_size=1,
            max_overflow=0,
            pool_timeout=3,
        )
        with engine.connect() as c, c.begin():
            c.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            c.execute(text("SET LOCAL statement_timeout='2s'"))
            c.execute(text("SET LOCAL lock_timeout='2s'"))
            row = c.execute(
                text("""SELECT jsonb_typeof(state->'paused'),state->>'paused',
                COALESCE(lease_until > now(),false),jsonb_typeof(state->'orders'),
                jsonb_typeof(state->'requests'),
                (SELECT count(*) FROM jsonb_object_keys(state->'orders')),
                (SELECT count(*) FROM jsonb_each_text(state->'requests') WHERE value IN ('UNKNOWN','SUBMITTING','CANCEL_REQUESTED','NOT_FOUND_UNRESOLVED'))
                FROM demo_accounts WHERE account_id=:account"""),
                dict(account="DEMO-WINDOWS-ISOLATED-BTC-V1"),
            ).one_or_none()
            if (
                row is None
                or row[0] != "boolean"
                or row[1] != "true"
                or row[2] is not False
                or row[3] != "object"
                or row[4] != "object"
            ):
                return result
            if (
                type(row[5]) is not int
                or not 0 <= row[5] <= 128
                or type(row[6]) is not int
                or not 0 <= row[6] <= 768
            ):
                return result
            result.update(
                status="PAUSED_NO_ACTIVE_LEASE",
                paused=True,
                active_lease=False,
                orders=row[5],
                unresolved_requests=row[6],
            )
        return result
    except Exception:
        return result
    finally:
        if engine is not None:
            try:
                engine.dispose()
            except Exception:
                pass


if __name__ == "__main__":
    report = collect()
    print(json.dumps(report, sort_keys=True))
    raise SystemExit(0 if report["status"] == "PAUSED_NO_ACTIVE_LEASE" else 2)
