"""Networkless, read-only diagnostics against a fresh copied PG/runtime pair."""

import base64
import hashlib
import json
import os
import re
import sys
import types
from pathlib import Path

MODULES = (
    ("scripts.review_demo_historical_receipts", "scripts/review_demo_historical_receipts.py"),
    ("services.execution.demo_copy_event_review", "services/execution/demo_copy_event_review.py"),
)


def collect(config, sources):
    try:
        os.environ.clear()
        sys.path.insert(0, "/app")
        if (
            set(config) != {"baseline_files", "candidate_files"}
            or len(config["baseline_files"]) != 89
        ):
            raise ValueError("COPY_CONFIG_INVALID")
        if set(config["candidate_files"]) != {p for _, p in MODULES} or set(sources) != set(
            config["candidate_files"]
        ):
            raise ValueError("COPY_CONFIG_INVALID")
        for p, h in config["baseline_files"].items():
            if not isinstance(p, str) or ".." in p or p.startswith("/") or not p.endswith(".py"):
                raise ValueError("COPY_BASELINE_INVALID")
            f = Path("/app") / p
            if f.stat().st_size > 2 * 1024**2 or hashlib.sha256(f.read_bytes()).hexdigest() != h:
                raise ValueError("COPY_BASELINE_MISMATCH")
        for name, p in MODULES:
            body = base64.b64decode(sources[p], validate=True)
            if (
                len(body) > 1024**2
                or hashlib.sha256(body).hexdigest() != config["candidate_files"][p]
            ):
                raise ValueError("COPY_CANDIDATE_MISMATCH")
            module = types.ModuleType(name)
            module.__file__ = "/in-memory-review/" + p
            module.__package__ = name.rpartition(".")[0]
            sys.modules[name] = module
            exec(compile(body, "<copy-review-candidate>", "exec"), module.__dict__)
        from sqlalchemy import create_engine, text
        from sqlalchemy.pool import NullPool

        from services.execution.demo_copy_event_review import review_copy_request_events

        engine = create_engine(
            "postgresql+psycopg://dev@/hybrid?host=/review-socket",
            hide_parameters=True,
            poolclass=NullPool,
            connect_args={
                "connect_timeout": 3,
                "options": "-c default_transaction_read_only=on -c statement_timeout=2000 -c timezone=UTC",
            },
        )
        try:
            with engine.connect() as c, c.begin():
                if c.execute(text("SHOW transaction_read_only")).scalar_one() != "on":
                    raise ValueError("COPY_SESSION_NOT_READ_ONLY")

            root = Path("/review-runtime")
            found = []
            for count, path in enumerate(root.rglob("*"), 1):
                if count > 50000 or path.is_symlink():
                    raise ValueError("COPY_EVENTS_RUNTIME_SCOPE_INVALID")
                if path.name == "intent.json" and re.fullmatch(
                    r"historical-request-evidence-[0-9a-f-]{36}", path.parent.name
                ):
                    if path.stat().st_size > 1024**2:
                        raise ValueError("COPY_EVENTS_PLAN_SIZE_LIMIT")
                    found.append(path)
            if len(found) != 1:
                raise ValueError("COPY_EVENTS_PLAN_NOT_UNIQUE")
            expected = json.loads(found[0].read_bytes())
            report = review_copy_request_events(engine, expected, copy_only_review=True)
            return report | dict(
                baseline_files_verified=89, candidate_files_verified=2, copy_session_read_only=True
            ), 0
        finally:
            engine.dispose()
    except Exception as error:
        # Never return raw SQL, connection strings, credential values or bodies.

        reason = "COPY_EVENTS_UNAVAILABLE"
        if (
            isinstance(error, ValueError)
            or any(c.__name__ == "LocalQuotaStopped" for c in type(error).__mro__)
        ) and re.fullmatch(r"[A-Z][A-Z0-9_]{1,100}", str(error)):
            reason = str(error)
        return dict(
            copy_event_review="BLOCKED",
            failure_category=reason,
            automatic_retry=False,
            execution_approved=False,
            orders_enabled=False,
            live_trading_enabled=False,
        ), 2


if __name__ == "__main__":
    value, code = collect(globals().get("CONFIG"), globals().get("SOURCES"))
    print(json.dumps(value, sort_keys=True))
    raise SystemExit(code)
