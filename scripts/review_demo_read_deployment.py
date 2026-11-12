"""Windows candidate-only read deployment review; no installation or audit apply."""

import hashlib
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import create_engine, select, text
from sqlalchemy.pool import NullPool

from packages.persistence.demo import accounts
from packages.persistence.demo_order_quota import LocalQuotaStopped
from services.execution.demo_models import DemoLedger
from services.execution.demo_read_audit_bootstrap import prepare_read_audit
from services.execution.demo_runtime_storage import RUNTIME_STORAGE_KEY, RuntimeClocks
from services.execution.demo_unused_read_clock import recover_unused_clock

ACCOUNT = "DEMO-WINDOWS-ISOLATED-BTC-V1"
SCOPE = "DEMO-WINDOWS-TP-MANAGED-WRITERS-V1"
SETUP_KEY = "MANAGED_RUNTIME:STORAGE_SETUP_20261111_V1"
SOURCE = "e03b6ba683732cd5872c30cf0d245a5b1fb6d325"
STORAGE = Path("/app/demo-export-state/demo-runtime-20261111/shared-weight")


def require(ok: bool, code: str) -> None:
    if not ok:
        raise LocalQuotaStopped(code)


def verify_files(root: Path, files: dict[str, str]) -> None:
    require(isinstance(files, dict) and 1 <= len(files) <= 512, "DEPLOYMENT_MANIFEST_INVALID")
    for name, expected in files.items():
        relative = Path(name)
        require(
            not relative.is_absolute() and ".." not in relative.parts, "DEPLOYMENT_MANIFEST_INVALID"
        )
        path = root / relative
        require(
            path.resolve().is_relative_to(root.resolve())
            and path.is_file()
            and not path.is_symlink(),
            "DEPLOYMENT_SOURCE_MISSING",
        )
        require(path.stat().st_size <= 2 * 1024 * 1024, "DEPLOYMENT_SOURCE_SIZE_INVALID")
        require(
            hashlib.sha256(path.read_bytes()).hexdigest() == expected,
            "DEPLOYMENT_SOURCE_HASH_MISMATCH",
        )


def review(
    candidate_root: Path,
    *,
    installed_root: Path = Path("/app"),
    clocks: RuntimeClocks | None = None,
    status_only: bool = False,
    readers_stopped: bool = False,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    manifest = json.loads((candidate_root / "CANDIDATE_MANIFEST.json").read_text())
    require(manifest["baseline_reference"] == SOURCE, "DEPLOYMENT_BASELINE_INVALID")
    if progress:
        progress("CANDIDATE_SOURCE_VALIDATION")
    verify_files(candidate_root, manifest["candidate_files"])
    if progress:
        progress("INSTALLED_SOURCE_VALIDATION")
    verify_files(installed_root, manifest["installed_files"])
    operation = manifest.get("operation", "review")
    require(
        operation != "apply-audit" or readers_stopped is True, "DEPLOYMENT_READERS_STOPPED_REQUIRED"
    )
    require(
        os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "false"
        and os.getenv("TRADING_MODE", "PAPER").upper() != "LIVE",
        "DEPLOYMENT_LIVE_CONTEXT_FORBIDDEN",
    )
    url = os.environ.get("DATABASE_URL", "")
    require(url.startswith("postgresql+psycopg://"), "DEPLOYMENT_DATABASE_CONFIGURATION_REQUIRED")
    if progress:
        progress("ACCOUNT_ENGINE_CREATION")
    engine = create_engine(
        url,
        poolclass=NullPool,
        hide_parameters=True,
        connect_args={
            "connect_timeout": 5,
            "options": "-c statement_timeout=2000 -c lock_timeout=2000",
        },
    )
    try:
        if progress:
            progress("ACCOUNT_RECEIPT_READ")
        with engine.connect() as c, c.begin():
            c.execute(text("SET TRANSACTION READ ONLY"))
            row = c.execute(
                select(accounts.c.state).where(accounts.c.account_id == ACCOUNT)
            ).scalar_one()
            ledger = DemoLedger.model_validate(row)
        record = json.loads(ledger.operations.get(SETUP_KEY, "{}"))
        binding = json.loads(ledger.operations.get(RUNTIME_STORAGE_KEY, "{}"))
        require(
            type(record.get("version")) is int
            and record["version"] == 1
            and record.get("account") == ACCOUNT
            and record.get("source") == SOURCE
            and record.get("scope") == SCOPE
            and record.get("directory") == "demo-runtime-20261111"
            and binding.get("scope") == SCOPE
            and binding.get("journal") == record.get("shared_journal"),
            "DEPLOYMENT_STORAGE_RECEIPTS_MISMATCH",
        )
        journal, installation = (
            UUID(record["shared_journal"]),
            UUID(binding["checkpoint_installation"]),
        )
        recovery = {}
        if operation in {"recover-unused-clock", "apply-audit"}:
            require(
                record.get("stage") == "PREPARED"
                and record.get("anchor_installation") == str(installation)
                and record.get("expected_runtime_binding")
                == ledger.operations[RUNTIME_STORAGE_KEY],
                "DEPLOYMENT_STORAGE_RECEIPTS_MISMATCH",
            )
        if operation == "recover-unused-clock":
            recovery = recover_unused_clock(
                engine,
                ACCOUNT,
                STORAGE,
                SCOPE,
                journal,
                installation,
                apply=True,
                status_only=status_only,
                clocks=clocks,
            )
            if not recovery["startup_ready"]:
                return recovery | dict(
                    read_deployment_review="WAIT_STARTUP_HOLD",
                    next_action="WAIT_THEN_REVIEW_KEEP_PAUSED",
                )
        else:
            require(operation in {"review", "apply-audit"}, "DEPLOYMENT_OPERATION_INVALID")
        applying = operation == "apply-audit"
        if applying and progress:
            progress("AUDIT_APPLY")
        if progress and not applying:
            progress("STORAGE_AUDIT_PLAN")
        report = prepare_read_audit(
            engine,
            ACCOUNT,
            STORAGE,
            SCOPE,
            journal,
            installation,
            apply=applying,
            readers_stopped=readers_stopped if applying else False,
            clocks=clocks,
        )
        if applying:
            if progress:
                progress("AUDIT_POSTCHECK")
            post = prepare_read_audit(
                engine, ACCOUNT, STORAGE, SCOPE, journal, installation, apply=False, clocks=clocks
            )
            require(
                not post["audit_schema_required"] and not post["audit_binding_required"],
                "DEPLOYMENT_AUDIT_POSTCHECK_FAILED",
            )
            report["audit_postcheck_passed"] = True
        return (
            report
            | recovery
            | dict(
                read_deployment_review="AUDIT_CONFIGURED_KEEP_PAUSED"
                if applying
                else "CANDIDATE_PLAN_VERIFIED_KEEP_PAUSED",
                baseline_source_verified=True,
                candidate_source_verified=True,
                candidate_reference=manifest["candidate_reference"],
                api_replaced=False,
                audit_apply_attempted=applying,
                full_deployment_completed=False,
            )
        )
    finally:
        engine.dispose()


def main() -> int:
    stage = ["SOURCE_AND_CONTEXT_VALIDATION"]
    try:
        report = review(
            Path(__file__).resolve().parents[1],
            status_only=sys.argv[2:] == ["--status"],
            readers_stopped=sys.argv[2:] == ["--readers-stopped"],
            progress=lambda value: stage.__setitem__(0, value),
        )
        code = 2 if report["read_deployment_review"] == "WAIT_STARTUP_HOLD" else 0
    except Exception as error:
        import re

        reason = (
            str(error)
            if isinstance(error, LocalQuotaStopped)
            and re.fullmatch(r"[A-Z][A-Z0-9_]{1,100}", str(error))
            else "DEPLOYMENT_REVIEW_UNAVAILABLE"
        )
        report = dict(
            read_deployment_review="BLOCKED_KEEP_PAUSED",
            stop_reason=reason,
            api_replaced=False,
            audit_apply_attempted=stage[0] in {"AUDIT_APPLY", "AUDIT_POSTCHECK"},
            order_writes=0,
            orders_enabled=False,
            live_trading_enabled=False,
            execution_approved=False,
            next_action="SHARE_OUTPUT_NO_AUTOMATIC_RETRY",
            resource_changes="UNKNOWN_IF_LOCAL_APPLY_STARTED",
            stage=stage[0],
        )
        code = 1
    print(json.dumps(report, sort_keys=True, allow_nan=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
