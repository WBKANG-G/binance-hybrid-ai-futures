"""Networkless, read-only diagnostics against a fresh copied PG/runtime pair."""

import base64
import hashlib
import json
import os
import re
import sys
import types
from datetime import UTC
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

MODULES = (
    (("scripts.demo_key_session", "scripts/demo_key_session.py"),)
    + MODULES
    + (
        (
            "services.execution.demo_read_credential_context",
            "services/execution/demo_read_credential_context.py",
        ),
        (
            "services.execution.demo_copy_volatile_credentials",
            "services/execution/demo_copy_volatile_credentials.py",
        ),
        (
            "services.execution.demo_private_read_scope",
            "services/execution/demo_private_read_scope.py",
        ),
        (
            "services.execution.demo_copy_private_preparation",
            "services/execution/demo_copy_private_preparation.py",
        ),
        (
            "services.execution.demo_copy_private_week_review",
            "services/execution/demo_copy_private_week_review.py",
        ),
    )
)


def collect(config, sources):
    try:
        os.environ.clear()
        sys.path.insert(0, "/app")
        if (
            set(config)
            != {
                "baseline_files",
                "candidate_files",
                "expected_snapshot_sha256",
                "role",
                "scope_policy",
                "review_day_kst",
            }
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
        from services.execution.demo_private_read_scope import validate_private_read_scope

        scope_arguments = dict(config["scope_policy"])
        for key in ("owned_container_ids", "forbidden_container_ids", "forbidden_volume_names"):
            scope_arguments[key] = set(scope_arguments[key])
        policy = validate_private_read_scope(**scope_arguments, enable_review=True)
        role = config["role"]
        if role not in {"REGISTER", "READ", "PROBE_EMPTY"}:
            raise ValueError("PRIVATE_WORKER_ROLE_INVALID")
        from scripts import demo_key_session as keys

        if role == "PROBE_EMPTY":
            fd = keys._directory()
            try:
                if os.listdir(fd):
                    raise ValueError("PRIVATE_KEYS_NOT_EMPTY_AFTER_STOP")
            finally:
                os.close(fd)
            return dict(private_key_session="EMPTY_AFTER_LAST_HOLDER_STOP", files_created=False), 0
        from datetime import datetime, timedelta, timezone

        if (
            config["review_day_kst"] != "2026-11-16"
            or datetime.now(UTC).astimezone(timezone(timedelta(hours=9))).date().isoformat()
            != "2026-11-16"
        ):
            raise ValueError("PRIVATE_REVIEW_DATE_MISMATCH")
        if role == "REGISTER":
            from services.execution.demo_copy_evidence_rehearsal import (
                SYNTHETIC_PRIVATE_KEY,
                SYNTHETIC_PRIVATE_SECRET,
            )

            values = iter((SYNTHETIC_PRIVATE_KEY, SYNTHETIC_PRIVATE_SECRET))
            keys.getpass.getpass = lambda *args: next(values)
            keys.register()
            return dict(
                private_key_session="SYNTHETIC_REGISTERED_IN_NEW_TMPFS", real_keys_used=False
            ), 0
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
            from services.execution.demo_copy_private_preparation import SyntheticPrivatePreparation
            from services.execution.demo_copy_private_week_review import rehearse_private_week_copy

            key_before = Path("/run/demo-keys/session.json").read_bytes()
            lease = SyntheticPrivatePreparation(
                "DEMO-WINDOWS-ISOLATED-BTC-V1", scope, synthetic_only=True, copy_only=True
            )
            print("PRIVATE_COPY_STAGE=PRIVATE_KEY_AND_WEEK_REVIEW", flush=True)

            def progress(stage):
                print("PRIVATE_COPY_STAGE=" + stage, flush=True)

            week = rehearse_private_week_copy(
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
                lease,
                copy_only=True,
                synthetic_only=True,
                acknowledge_week_boundary_observation=True,
                progress=progress,
            )
            if Path("/run/demo-keys/session.json").read_bytes() != key_before:
                raise ValueError("PRIVATE_KEYS_CHANGED_DURING_REVIEW")
            pipeline = week["pipeline"]
            return report | dict(
                synthetic_evidence_rehearsal=pipeline,
                private_week_review=week,
                private_scope_policy=policy,
                synthetic_key_file_unchanged=True,
                baseline_files_verified=89,
                candidate_files_verified=16,
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
            private_timing_reasons=list(getattr(error, "private_timing_reasons", ())),
            automatic_retry=False,
            execution_approved=False,
            orders_enabled=False,
            live_trading_enabled=False,
        ), 2


if __name__ == "__main__":
    value, code = collect(globals().get("CONFIG"), globals().get("SOURCES"))
    print(json.dumps(value, sort_keys=True))
    raise SystemExit(code)
