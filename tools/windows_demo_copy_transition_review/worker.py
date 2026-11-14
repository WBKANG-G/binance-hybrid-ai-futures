"""Networkless, read-only diagnostics against a fresh copied PG/runtime pair."""

import base64
import hashlib
import json
import os
import re
import sys
import types
from pathlib import Path
from uuid import UUID

MODULES = (
    (
        "packages.persistence.demo_response_evidence",
        "packages/persistence/demo_response_evidence.py",
    ),
    ("services.execution.demo_evidence_snapshot", "services/execution/demo_evidence_snapshot.py"),
    (
        "services.execution.demo_copy_transition_review",
        "services/execution/demo_copy_transition_review.py",
    ),
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
        from sqlalchemy import create_engine, select, text
        from sqlalchemy.pool import NullPool

        from packages.persistence.demo import accounts
        from services.execution.demo_copy_transition_review import review_copy_transition
        from services.execution.demo_runtime_storage import RUNTIME_STORAGE_KEY

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
                state = c.execute(
                    select(accounts.c.state).where(
                        accounts.c.account_id == "DEMO-WINDOWS-ISOLATED-BTC-V1"
                    )
                ).scalar_one()
            binding = json.loads(state["operations"][RUNTIME_STORAGE_KEY])
            scope = binding["scope"]
            if not isinstance(scope, str) or not scope.startswith("DEMO-") or len(scope) > 80:
                raise ValueError("COPY_SCOPE_INVALID")
            target = hashlib.sha256(scope.encode()).hexdigest() + ".weight.sqlite3"
            root = Path("/review-runtime")
            found = []
            for count, path in enumerate(root.rglob("*"), 1):
                if count > 50000 or path.is_symlink():
                    raise ValueError("COPY_RUNTIME_SCOPE_INVALID")
                if path.name == target:
                    found.append(path.parent)
            if len(found) != 1:
                raise ValueError("COPY_RUNTIME_SCOPE_INVALID")
            report = review_copy_transition(
                engine,
                "DEMO-WINDOWS-ISOLATED-BTC-V1",
                found[0],
                scope,
                UUID(binding["journal"]),
                UUID(binding["checkpoint_installation"]),
                copy_only_review=True,
            )
            return report | dict(
                baseline_files_verified=89, candidate_files_verified=3, copy_session_read_only=True
            ), 0
        finally:
            engine.dispose()
    except Exception as error:
        # Never return raw SQL, connection strings, credential values or bodies.

        reason = "COPY_DIAGNOSTIC_UNAVAILABLE"
        if (
            isinstance(error, ValueError)
            or any(c.__name__ == "LocalQuotaStopped" for c in type(error).__mro__)
        ) and re.fullmatch(r"[A-Z][A-Z0-9_]{1,100}", str(error)):
            reason = str(error)
        return dict(
            copy_transition_review="BLOCKED",
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
