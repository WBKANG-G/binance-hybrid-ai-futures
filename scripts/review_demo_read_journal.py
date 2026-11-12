"""Explicit local audit/recovery entrypoint. Never provisions or sends exchange requests."""

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any, NoReturn
from uuid import UUID

from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from packages.persistence.demo_order_quota import LocalQuotaStopped
from packages.persistence.demo_read_audit import AuditedDemoReadJournal, ReadAuditStore
from packages.persistence.demo_read_recovery import recover_completed_read_clock
from packages.persistence.demo_shared_checkpoint import SharedWeightAnchorStore
from services.execution.demo_read_audit_bootstrap import prepare_read_audit
from services.execution.demo_runtime_storage import RuntimeClocks


class ArgumentsInvalid(ValueError):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise ArgumentsInvalid("READ_OPERATION_ARGUMENTS_INVALID")


def arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = Parser(description=__doc__, allow_abbrev=False)
    parser.add_argument("operation", choices=("review", "recover-clock", "prepare-audit"))
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--scope", required=True)
    parser.add_argument("--journal", type=UUID, required=True)
    parser.add_argument("--installation", type=UUID, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--readers-stopped", action="store_true")
    parser.add_argument("--account")
    args = parser.parse_args(argv)
    if (
        not args.directory.is_absolute()
        or not re.fullmatch(r"DEMO-[A-Za-z0-9_-]{1,75}", args.scope)
        or (args.operation == "review" and (args.apply or args.readers_stopped))
        or (args.operation == "recover-clock" and not (args.apply and args.readers_stopped))
        or (args.operation != "prepare-audit" and args.account is not None)
        or (
            args.operation == "prepare-audit"
            and (
                not args.account
                or not re.fullmatch(r"DEMO-[A-Za-z0-9_-]{1,75}", args.account)
                or (args.apply and not args.readers_stopped)
            )
        )
    ):
        raise ArgumentsInvalid("READ_OPERATION_ARGUMENTS_INVALID")
    return args


def run(
    argv: list[str] | None = None, *, clocks: RuntimeClocks | None = None
) -> tuple[int, dict[str, Any]]:
    base: dict[str, Any] = dict(
        execution_approved=False,
        orders_enabled=False,
        live_trading_enabled=False,
        order_writes=0,
        exchange_http_attempts=0,
        exchange_credentials_accessed=False,
        financial_ledger_changed=False,
        period_baselines_changed=False,
        account_state_checked=False,
        automatic_retry=False,
        next_action="KEEP_PAUSED_REVIEW_ONLY",
    )
    engine = None
    write_requested = False
    stage = "ARGUMENTS"
    try:
        args = arguments(argv)
        write_requested = args.apply
        stage = "ENVIRONMENT"
        if (
            os.getenv("LIVE_TRADING_ENABLED", "false").lower() != "false"
            or os.getenv("TRADING_MODE", "PAPER").upper() == "LIVE"
        ):
            raise ArgumentsInvalid("READ_OPERATION_LIVE_CONTEXT_FORBIDDEN")
        url = os.environ.get("DATABASE_URL", "")
        if not url.startswith("postgresql+psycopg://"):
            raise ArgumentsInvalid("READ_OPERATION_DATABASE_CONFIGURATION_REQUIRED")
        stage = "INSTALLATION_VALIDATION"
        engine = create_engine(
            url,
            poolclass=NullPool,
            hide_parameters=True,
            connect_args={
                "connect_timeout": 5,
                "options": "-c statement_timeout=2000 -c lock_timeout=2000",
            },
        )
        anchor, audit = SharedWeightAnchorStore(engine), ReadAuditStore(engine)
        if anchor.installation_id() != args.installation:
            raise ArgumentsInvalid("READ_OPERATION_INSTALLATION_MISMATCH")
        current = clocks or RuntimeClocks()
        stage = {
            "review": "JOURNAL_REVIEW",
            "recover-clock": "CLOCK_RECOVERY",
            "prepare-audit": "AUDIT_SETUP",
        }[args.operation]
        if args.operation == "prepare-audit":
            report = prepare_read_audit(
                engine,
                args.account,
                args.directory,
                args.scope,
                args.journal,
                args.installation,
                apply=args.apply,
                readers_stopped=args.readers_stopped,
                clocks=current,
            )
            return 0, base | report | dict(stage=stage, account_state_checked=True)
        if args.operation == "review":
            shared = AuditedDemoReadJournal(
                args.directory,
                args.scope,
                args.journal,
                anchors=anchor,
                audit=audit,
                read_only=True,
                monotonic=current.monotonic,
                wall_clock=current.wall_clock,
                boot=current.boot,
            )
            report = audit.review(shared)
        else:
            report = recover_completed_read_clock(
                args.directory,
                args.scope,
                args.journal,
                anchor=anchor,
                audit=audit,
                apply=True,
                clocks=current,
            )
        complete = report["local_read_activity_complete"]
        code = 3 if not complete else 0 if report["startup_ready"] else 2
        return code, report | base | dict(
            read_journal_operation="REVIEW_REQUIRED_PENDING"
            if not complete
            else "LOCAL_REVIEW_COMPLETE_KEEP_PAUSED"
            if code == 0
            else "WAIT_STARTUP_HOLD",
            stage=stage,
            readers_stopped_attested=args.readers_stopped,
            readers_stopped_independently_verified=False,
        )
    except (ArgumentsInvalid, LocalQuotaStopped) as error:
        # Only typed control codes are public; no SQL, URLs, paths, or exception bodies.
        value = str(error)
        reason = (
            value if re.fullmatch(r"[A-Z][A-Z0-9_]{1,100}", value) else "READ_OPERATION_REFUSED"
        )
    except Exception:
        reason = "READ_OPERATION_UNAVAILABLE"
    finally:
        if engine is not None:
            engine.dispose()
    return 1, base | dict(
        read_journal_operation="BLOCKED_KEEP_PAUSED",
        stage=stage,
        stop_reason=reason,
        resource_changes="UNKNOWN_IF_APPLY_STARTED"
        if stage in {"CLOCK_RECOVERY", "AUDIT_SETUP"} and write_requested
        else "NONE_BY_THIS_COMMAND",
    )


def main() -> int:
    code, report = run()
    print(json.dumps(report, sort_keys=True, allow_nan=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
