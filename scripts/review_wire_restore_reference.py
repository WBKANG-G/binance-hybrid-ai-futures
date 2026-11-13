"""Offline-by-default, read-only synthetic reference review. Never provision/restore/send."""

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, NoReturn
from uuid import UUID

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url

from packages.persistence.demo_order_quota import LocalQuotaStopped
from packages.persistence.demo_read_audit import digest, require
from packages.persistence.demo_read_recovery import _file, _private_directory
from packages.persistence.demo_wire_checkpoint_candidate import WireCheckpointStore
from services.execution.demo_wire_restore_reference_candidate import (
    MAX_MANIFEST,
    _separate,
    parse_reference,
    review_reference_file,
)
from services.execution.demo_wire_session_contract_candidate import _hash, _scope, _uuid

DATABASE_ENV = "WIRE_REVIEW_DATABASE_URL"
PUBLIC_CODES = frozenset(
    {
        "WIRE_REVIEW_COMPARE_DISABLED",
        "WIRE_REVIEW_EXPLICIT_TARGET_REQUIRED",
        "WIRE_REVIEW_INSPECT_HAS_NO_DATABASE_TARGET",
        "WIRE_REVIEW_DATABASE_ENV_REQUIRED",
        "WIRE_REVIEW_DATABASE_DRIVER_UNSUPPORTED",
        "WIRE_REFERENCE_RETAINED_DIGEST_MISMATCH",
        "WIRE_REFERENCE_EXPECTED_BINDING_MISMATCH",
        "WIRE_REFERENCE_SEPARATE_DIRECTORY_REQUIRED",
        "WIRE_REFERENCE_SEPARATELY_RETAINED_DIGEST_REQUIRED",
        "WIRE_REFERENCE_SCHEMA_INVALID",
        "WIRE_REFERENCE_BODY_LIMIT",
        "WIRE_REFERENCE_CANONICAL_BYTES_REQUIRED",
        "WIRE_REFERENCE_HISTORY_PREFLIGHT_LIMIT",
        "WIRE_REFERENCE_HISTORY_TIME_BUDGET",
        "WIRE_REFERENCE_HISTORY_METADATA_INVALID",
        "WIRE_REFERENCE_HISTORY_COVERAGE_INVALID",
        "WIRE_REFERENCE_ACCOUNT_PREFLIGHT_LIMIT",
        "WIRE_REFERENCE_ACCOUNT_STATE_INVALID",
        "WIRE_ANCHOR_SCHEMA_INVALID",
        "WIRE_ANCHOR_BINDING_MISSING",
        "WIRE_ANCHOR_CHECKPOINT_GAP",
        "WIRE_ANCHOR_WITNESS_MISMATCH",
        "WIRE_ANCHOR_FINANCIAL_WITNESS_INVALID",
        "WIRE_ANCHOR_FINANCIAL_MARKER_COVERAGE_MISMATCH",
        "WIRE_ANCHOR_ROOT_INVALID",
        "WIRE_ANCHOR_CHECKPOINT_INVALID",
        "WIRE_ANCHOR_SOURCE_PIN_MISMATCH",
        "WIRE_CONTRACT_UUID_INVALID",
        "WIRE_CONTRACT_DIGEST_INVALID",
        "WIRE_CONTRACT_CHAIN_INVALID",
        "WIRE_CONTRACT_LOG_INVALID",
        "WIRE_CONTRACT_RECORD_LIMIT",
        "READ_RECOVERY_PRIVATE_DIRECTORY_REQUIRED",
        "READ_RECOVERY_BACKUP_INVALID",
        "SHARED_FILE_IDENTITY_INVALID",
        "REQUEST_OUTCOME_JSON_INVALID",
        "REQUEST_OUTCOME_JSON_OBJECT_REQUIRED",
        "REQUEST_OUTCOME_JSON_DUPLICATE_KEY",
        "REQUEST_OUTCOME_JSON_NONFINITE",
    }
)


def _blocked(code: str) -> dict[str, Any]:
    return dict(
        reference_cli_review="BLOCKED_KEEP_PAUSED",
        failure_code=code,
        database_changed=False,
        filesystem_changed=False,
        actual_http_attempts=0,
        order_writes=0,
        restore_performed=False,
        storage_restore_authorized=False,
        process_restart_resumption_supported=False,
        credential_session_resumed=False,
        external_reference_authenticity_verified=False,
        reference_is_latest_proven=False,
        whole_system_rollback_protection_verified=False,
        whole_runtime_backup_verified=False,
        remote_account_identity_verified=False,
        request_states_changed=False,
        execution_approved=False,
        orders_enabled=False,
        live_trading_enabled=False,
    )


def _emit(report: dict[str, Any]) -> None:
    print(json.dumps(report, sort_keys=True))


class PrivateArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        # argparse's default error includes rejected values, potentially credentials.
        _emit(_blocked("WIRE_REVIEW_ARGUMENTS_INVALID"))
        self.exit(2)


def parser() -> argparse.ArgumentParser:
    result = PrivateArgumentParser(
        description="합성 journal 참조의 읽기 전용 검사. 복원·실행 승인을 부여하지 않습니다."
    )
    result.add_argument(
        "--mode",
        choices=("inspect", "compare"),
        default="inspect",
        help="기본 inspect는 DB 접속 없이 파일과 제공한 digest만 검사합니다.",
    )
    result.add_argument("--reference-file", required=True)
    result.add_argument(
        "--retained-sha256",
        required=True,
        help="독립 보관한 참조 digest. 현재 파일에서 자동 생성하지 않습니다.",
    )
    result.add_argument("--source-directory")
    result.add_argument("--account")
    result.add_argument("--scope")
    result.add_argument("--journal")
    result.add_argument(
        "--enable-synthetic-review",
        action="store_true",
        help="compare 선택 시 필요. 실제 주문이나 writer를 활성화하지 않습니다.",
    )
    return result


def _reference(path: Path, retained: str) -> dict[str, Any]:
    _hash(retained)
    _private_directory(path.parent)
    body = _file(path, MAX_MANIFEST)
    require(hashlib.sha256(body).hexdigest() == retained, "WIRE_REFERENCE_RETAINED_DIGEST_MISMATCH")
    return parse_reference(body)


def _inspect(data: dict[str, Any], retained: str) -> dict[str, Any]:
    return dict(
        reference_cli_review="SUPPLIED_DIGEST_MATCHED_OFFLINE_KEEP_PAUSED",
        mode="inspect",
        supplied_reference_sha256=retained,
        binding_sha256=digest(data["binding"]),
        records=data["state"]["records"],
        pending_operations=data["state"]["pending_operations"],
        unresolved_local_requests=data["state"]["unresolved_local_requests"],
        database_accessed=False,
        database_changed=False,
        filesystem_changed=False,
        source_snapshot_compared=False,
        whole_system_rollback_protection_verified=False,
        whole_runtime_backup_verified=False,
        remote_account_identity_verified=False,
        request_states_changed=False,
        external_reference_authenticity_verified=False,
        reference_is_latest_proven=False,
        storage_restore_authorized=False,
        restore_performed=False,
        process_restart_resumption_supported=False,
        credential_session_resumed=False,
        actual_http_attempts=0,
        order_writes=0,
        execution_approved=False,
        orders_enabled=False,
        live_trading_enabled=False,
    )


def _safe_failure(error: Exception) -> dict[str, Any]:
    if isinstance(error, LocalQuotaStopped):
        code = str(error)
        if code in PUBLIC_CODES:
            return _blocked(code)
    if isinstance(error, OSError):
        return _blocked("WIRE_REVIEW_FILE_UNAVAILABLE_OR_UNSAFE")
    # Never stringify database/URL/parser exceptions or print a traceback.
    return _blocked("WIRE_REVIEW_SOURCE_OR_INPUT_FAILED")


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    engine: Engine | None = None
    report: dict[str, Any]
    status = 2
    try:
        if args.mode == "compare":
            require(args.enable_synthetic_review is True, "WIRE_REVIEW_COMPARE_DISABLED")
            require(
                all(
                    isinstance(v, str) and v
                    for v in (args.source_directory, args.account, args.scope, args.journal)
                ),
                "WIRE_REVIEW_EXPLICIT_TARGET_REQUIRED",
            )
            _scope(args.account, args.scope)
            _uuid(args.journal)
        else:
            require(
                not args.enable_synthetic_review
                and all(
                    v is None
                    for v in (args.source_directory, args.account, args.scope, args.journal)
                ),
                "WIRE_REVIEW_INSPECT_HAS_NO_DATABASE_TARGET",
            )
        path = Path(args.reference_file)
        data = _reference(path, args.retained_sha256)
        if args.mode == "inspect":
            report, status = _inspect(data, args.retained_sha256), 0
        else:
            require(
                data["binding"]["account"] == args.account
                and data["binding"]["scope"] == args.scope
                and data["binding"]["journal"] == args.journal,
                "WIRE_REFERENCE_EXPECTED_BINDING_MISMATCH",
            )
            source = Path(args.source_directory)
            _separate(path.parent, source)
            configured = os.environ.get(DATABASE_ENV)
            require(bool(configured), "WIRE_REVIEW_DATABASE_ENV_REQUIRED")
            assert configured is not None
            url = make_url(configured)
            require(
                url.drivername == "postgresql+psycopg", "WIRE_REVIEW_DATABASE_DRIVER_UNSUPPORTED"
            )
            engine = create_engine(
                url,
                echo=False,
                hide_parameters=True,
                pool_size=1,
                max_overflow=0,
                pool_timeout=3,
                connect_args={"connect_timeout": 3},
            )
            report = review_reference_file(
                WireCheckpointStore(engine),
                source,
                args.scope,
                UUID(args.journal),
                args.account,
                path,
                retained_sha256=args.retained_sha256,
            )
            matched = report["matches_supplied_retained_reference"]
            report.update(
                reference_cli_review="SOURCE_MATCHED_REFERENCE_KEEP_PAUSED"
                if matched
                else "BLOCKED_REFERENCE_MISMATCH_KEEP_PAUSED",
                mode="compare",
            )
            status = 0 if matched else 2
    except Exception as error:
        report = _safe_failure(error)
    finally:
        if engine is not None:
            try:
                engine.dispose()
            except Exception:
                report = _blocked("WIRE_REVIEW_SOURCE_CLEANUP_FAILED")
                status = 2
    _emit(report)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
