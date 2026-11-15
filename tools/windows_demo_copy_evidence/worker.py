"""Networkless, read-only diagnostics against a fresh copied PG/runtime pair."""

import base64
import hashlib
import json
import os
import re
import sys
import types
from pathlib import Path
from uuid import UUID, uuid4

MODULES = (
    ("packages.persistence.demo_read_recovery", "packages/persistence/demo_read_recovery.py"),
    (
        "packages.persistence.demo_response_evidence",
        "packages/persistence/demo_response_evidence.py",
    ),
    ("services.execution.demo_evidence_snapshot", "services/execution/demo_evidence_snapshot.py"),
    (
        "services.execution.demo_copy_transition_review",
        "services/execution/demo_copy_transition_review.py",
    ),
    (
        "services.execution.demo_copy_clock_rehearsal",
        "services/execution/demo_copy_clock_rehearsal.py",
    ),
)


MODULES = MODULES + (
    (
        "services.execution.demo_response_evidence_transport",
        "services/execution/demo_response_evidence_transport.py",
    ),
    (
        "services.execution.demo_copy_baseline_evidence",
        "services/execution/demo_copy_baseline_evidence.py",
    ),
    (
        "services.execution.demo_copy_response_archive",
        "services/execution/demo_copy_response_archive.py",
    ),
    (
        "services.execution.demo_copy_account_content",
        "services/execution/demo_copy_account_content.py",
    ),
    (
        "services.execution.demo_copy_evidence_rehearsal",
        "services/execution/demo_copy_evidence_rehearsal.py",
    ),
)


def collect(config, sources):
    try:
        os.environ.clear()
        sys.path.insert(0, "/app")
        if (
            set(config) != {"baseline_files", "candidate_files", "expected_snapshot_sha256"}
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
        from services.execution.demo_copy_clock_rehearsal import rehearse_copy_clock
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
        writer = create_engine(
            "postgresql+psycopg://dev@/hybrid?host=/review-socket",
            hide_parameters=True,
            poolclass=NullPool,
            connect_args={
                "connect_timeout": 3,
                "options": "-c default_transaction_read_only=off -c statement_timeout=2000 -c timezone=UTC",
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

            def progress(remaining):
                print("COPY_CLOCK_HOLD remaining_seconds=" + str(remaining), flush=True)

            report = rehearse_copy_clock(
                engine,
                writer,
                "DEMO-WINDOWS-ISOLATED-BTC-V1",
                found[0],
                scope,
                UUID(binding["journal"]),
                UUID(binding["checkpoint_installation"]),
                config["expected_snapshot_sha256"],
                apply=True,
                copy_only=True,
                progress=progress,
            )
            from services.execution.demo_copy_evidence_rehearsal import (
                rehearse_synthetic_copy_evidence,
            )

            def pipeline_progress(stage):
                print("COPY_EVIDENCE_STAGE=" + stage, flush=True)

            pipeline = rehearse_synthetic_copy_evidence(
                engine,
                writer,
                "DEMO-WINDOWS-ISOLATED-BTC-V1",
                found[0],
                scope,
                UUID(binding["journal"]),
                UUID(binding["checkpoint_installation"]),
                report["after_snapshot_sha256"],
                found[0] / ("synthetic-response-" + uuid4().hex),
                b'{"totalWalletBalance": "12", "totalMarginBalance": "12", "availableBalance": "12", "totalUnrealizedProfit": "0", "totalInitialMargin": "0", "totalMaintMargin": "0", "totalPositionInitialMargin": "0", "totalOpenOrderInitialMargin": "0", "assets": [{"asset": "USDT", "walletBalance": "12", "marginBalance": "12", "availableBalance": "12", "unrealizedProfit": "0", "initialMargin": "0", "maintMargin": "0", "positionInitialMargin": "0", "openOrderInitialMargin": "0"}], "positions": [{"symbol": "BTCUSDT", "positionSide": "BOTH", "positionAmt": "0", "unrealizedProfit": "0", "initialMargin": "0", "maintMargin": "0", "positionInitialMargin": "0", "openOrderInitialMargin": "0"}]}',
                copy_only=True,
                synthetic_only=True,
                progress=pipeline_progress,
            )
            return report | dict(
                synthetic_evidence_rehearsal=pipeline,
                baseline_files_verified=89,
                candidate_files_verified=10,
                copy_read_sessions_read_only=True,
                copy_write_session_explicit=True,
            ), 0
        finally:
            engine.dispose()
            writer.dispose()
    except Exception as error:
        # Never return raw SQL, connection strings, credential values or bodies.

        reason = "COPY_DIAGNOSTIC_UNAVAILABLE"
        if (
            isinstance(error, ValueError)
            or any(c.__name__ == "LocalQuotaStopped" for c in type(error).__mro__)
        ) and re.fullmatch(r"[A-Z][A-Z0-9_]{1,100}", str(error)):
            reason = str(error)
        return dict(
            copy_clock_rehearsal="BLOCKED_COPY_MAY_HAVE_CHANGED",
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
