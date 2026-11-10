"""Offline consistency review of saved synthetic runtime reports, never admission."""

import argparse
import hashlib
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any, NoReturn

from scripts.review_tp_minute_budget import MAX_BYTES, TraceReviewInvalid
from scripts.review_tp_minute_budget import review as review_trace

READER_POLICY = "SYNTHETIC_CONFIRMATION_CONSISTENCY_V1"
RECEIPT_VERSION = 1
MAX_RECEIPT_BYTES = 64 * 1024


class ConfirmationReportInvalid(ValueError):
    pass


class ReceiptStorageInvalid(ConfirmationReportInvalid):
    def __init__(
        self,
        code: str,
        *,
        publication_attempted: bool = False,
        publication_confirmed: bool = False,
        cleanup_incomplete: bool = False,
    ):
        super().__init__(code)
        self.publication_attempted = publication_attempted
        self.publication_confirmed = publication_confirmed
        self.cleanup_incomplete = cleanup_incomplete


class ReportArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise ConfirmationReportInvalid("ARGUMENTS_INVALID")


BOOLS = {
    "confirmation_started",
    "confirmation_call_active",
    "confirmation_closed",
    "quota_seal_failed",
    "session_failed",
    "bridge_degraded",
    "quota_seal_verified_from_records",
    "financial_database_rechecked",
    "in_flight_work_force_canceled",
    "whole_function_wall_time_limit_proven",
    "execution_approved",
}
COUNTS = {
    "repeated_calls_refused",
    "closed_calls_refused",
    "completed_confirmation_attempts",
    "binding_rejections",
    "deferrals",
}
REASONS = {
    "NOT_STARTED",
    "RUNNING",
    "EXPOSURE_REMAINS",
    "DEFERRAL_LIMIT_REACHED",
    "ORIGINAL_PROOF_VERIFIED",
    "START_DEADLINE_REACHED",
    "CLOCK_UNTRUSTED",
    "CALLER_CANCELLED",
    "ORIGINAL_OPERATION_FAILED",
    "ATTEMPT_LIMIT_REACHED",
}


def require(ok: bool, code: str) -> None:
    if not ok:
        raise ConfirmationReportInvalid(code)


def count(value: Any, maximum: int = 1_000_000) -> int:
    require(type(value) is int and 0 <= value <= maximum, "COUNT_INVALID")
    return int(value)


def expected_outcome(d: dict[str, Any]) -> str:
    if not d["confirmation_started"]:
        return "NOT_STARTED"
    if d["returned_flat_confirmation"] == "VERIFIED_FLAT":
        suffix = (
            "QUOTA_SEAL_FAILED"
            if d["quota_seal_failed"]
            else "SESSION_FAILED"
            if d["session_failed"]
            else "RUNTIME_DEGRADED"
            if d["bridge_degraded"]
            else "REVIEW_ONLY"
        )
        return "PROOF_VERIFIED_" + suffix
    reason = d["stop_reason"]
    if reason == "CALLER_CANCELLED":
        return "CALLER_CANCELLED_PROOF_UNCONFIRMED"
    if reason == "START_DEADLINE_REACHED":
        return "START_DEADLINE_PROOF_UNCONFIRMED"
    if reason in {"ATTEMPT_LIMIT_REACHED", "DEFERRAL_LIMIT_REACHED"}:
        return "LIMIT_REACHED_PROOF_UNCONFIRMED"
    if d["binding_rejections"]:
        return "CONTEXT_REJECTED_PROOF_UNCONFIRMED"
    if reason == "ORIGINAL_OPERATION_FAILED":
        return "OPERATION_FAILED_PROOF_UNCONFIRMED"
    return "PROOF_UNCONFIRMED"


def review(data: Any) -> dict[str, Any]:
    require(
        isinstance(data, dict) and data.get("synthetic_only") is True, "SYNTHETIC_REPORT_REQUIRED"
    )
    require(data.get("closed_runtime") is True, "CLOSED_REPORT_REQUIRED")
    require(type(data.get("startup_holds_accelerated")) is bool, "STARTUP_SCOPE_REQUIRED")
    observation = data.get("observation_seconds")
    require(
        type(observation) in {int, float} and 0 < observation <= 300 and math.isfinite(observation),
        "OBSERVATION_INVALID",
    )
    d, result = data.get("confirmation_scheduler"), data.get("result")
    require(isinstance(d, dict) and isinstance(result, dict), "CONFIRMATION_FIELDS_REQUIRED")
    assert isinstance(d, dict) and isinstance(result, dict)
    for key in BOOLS:
        require(type(d.get(key)) is bool, "BOOLEAN_FIELD_REQUIRED")
    for key in COUNTS:
        count(d.get(key))
    for key, value in {
        "start_deadline_seconds": 120,
        "completed_attempt_limit": 4,
        "deferral_limit": 120,
    }.items():
        require(type(d.get(key)) is int and d[key] == value, "POLICY_MISMATCH")
    require(d.get("start_scope") == "ONE_CALL_PER_OWNED_COORDINATOR", "POLICY_MISMATCH")
    require(d.get("next_action") == "KEEP_PAUSED_REVIEW_ONLY", "REVIEW_SCOPE_REQUIRED")
    for key in {
        "execution_approved",
        "financial_database_rechecked",
        "quota_seal_verified_from_records",
        "in_flight_work_force_canceled",
        "whole_function_wall_time_limit_proven",
    }:
        require(d[key] is False, "UNSUPPORTED_APPROVAL_OR_PROOF_CLAIM")
    require(
        not d["confirmation_call_active"] and d["confirmation_closed"], "FINALIZATION_INCOMPLETE"
    )
    require(
        isinstance(d.get("stop_reason"), str) and d["stop_reason"] in REASONS, "STOP_REASON_INVALID"
    )
    require(d["stop_reason"] != "RUNNING", "FINALIZATION_INCOMPLETE")
    require(
        isinstance(d.get("returned_flat_confirmation"), str)
        and d["returned_flat_confirmation"] in {"UNKNOWN", "VERIFIED_FLAT"},
        "PROOF_STATUS_INVALID",
    )
    attempts, deferrals = d["completed_confirmation_attempts"], d["deferrals"]
    require(attempts <= 4 and deferrals <= 120, "ATTEMPT_LIMIT_MISMATCH")
    if not d["confirmation_started"]:
        require(
            d["stop_reason"] == "NOT_STARTED"
            and attempts == 0
            and deferrals == 0
            and d["returned_flat_confirmation"] == "UNKNOWN",
            "OWNERSHIP_MISMATCH",
        )
    else:
        require(d["stop_reason"] != "NOT_STARTED", "OWNERSHIP_MISMATCH")
    verified = d["returned_flat_confirmation"] == "VERIFIED_FLAT"
    require(verified == (d["stop_reason"] == "ORIGINAL_PROOF_VERIFIED"), "PROOF_REASON_MISMATCH")
    if verified:
        require(
            d["confirmation_started"] and attempts >= 1 and deferrals < 120,
            "PROOF_ATTEMPT_MISMATCH",
        )
    if d["stop_reason"] == "ATTEMPT_LIMIT_REACHED":
        require(attempts == 4, "ATTEMPT_LIMIT_MISMATCH")
    if d["stop_reason"] == "DEFERRAL_LIMIT_REACHED":
        require(deferrals == 120, "ATTEMPT_LIMIT_MISMATCH")
    if d["quota_seal_failed"]:
        require(d["session_failed"] and d["bridge_degraded"], "SEAL_FAILURE_FLAGS_MISMATCH")
    outcome = expected_outcome(d)
    require(d.get("reported_outcome") == outcome, "OUTCOME_MISMATCH")
    require(
        result.get("flat_confirmation") == d["returned_flat_confirmation"], "RESULT_PROOF_MISMATCH"
    )
    require(result.get("final_paused") is True, "PAUSED_RESULT_REQUIRED")
    require(
        result.get("orders_enabled") is False and result.get("live_trading_enabled") is False,
        "DISABLED_RESULT_REQUIRED",
    )
    unsealed = count(data.get("unsealed_flow_count"))
    entry = count(result.get("new_entry_post_count"), 1)
    exit_posts = count(result.get("operator_exit_post_attempts"), 1)
    try:
        trace = review_trace(data)
    except TraceReviewInvalid as error:
        raise ConfirmationReportInvalid("TRACE_" + error.code) from None
    posts = sum(
        row["method"] == "POST" and row["path"] == "/fapi/v1/order" for row in data["trace"]
    )
    require(posts == entry + exit_posts, "ORDER_POST_COUNT_MISMATCH")
    if not trace["counter_evidence_consistent"]:
        disposition = "REVIEW_REQUIRED_TRACE_INCONSISTENT"
    elif trace["unresolved_responses"]:
        disposition = "REVIEW_REQUIRED_TRACE_UNRESOLVED"
    elif trace["conclusion"] != "MEASURED_REQUESTS_FIT_ASSUMPTIONS_ONLY":
        disposition = "REVIEW_REQUIRED_TRACE_HEADROOM"
    elif d["quota_seal_failed"]:
        disposition = "REVIEW_REQUIRED_QUOTA_SEAL_FAILED"
    elif unsealed:
        disposition = "REVIEW_REQUIRED_UNSEALED_FLOWS"
    elif not verified:
        disposition = "REVIEW_REQUIRED_PROOF_UNCONFIRMED"
    elif d["session_failed"] or d["bridge_degraded"]:
        disposition = "REVIEW_REQUIRED_RUNTIME_FAILURE"
    else:
        disposition = "CONSISTENT_REPORT_ONLY_NO_APPROVAL"
    return dict(
        review="OFFLINE_SYNTHETIC_CONFIRMATION_CONSISTENCY_ONLY",
        version=1,
        reported_outcome=outcome,
        disposition=disposition,
        attention_required=disposition != "CONSISTENT_REPORT_ONLY_NO_APPROVAL",
        returned_flat_confirmation=d["returned_flat_confirmation"],
        quota_seal_failed=d["quota_seal_failed"],
        unsealed_flow_count=unsealed,
        trace_conclusion=trace["conclusion"],
        guarded_modeled_peak=trace["guarded_peak"]["weight"],
        input_authenticity_verified=False,
        financial_proof_reverified=False,
        current_exchange_state_verified=False,
        order_payload_scopes_verified=False,
        runtime_admission_changed=False,
        cost_upper_bounds_proven=False,
        order_count_limits_proven=False,
        shared_ip_capacity_reserved=False,
        execution_approved=False,
        next_action="KEEP_PAUSED_REVIEW_ONLY",
    )


def load(raw: bytes) -> Any:
    require(len(raw) <= MAX_BYTES, "FILE_TOO_LARGE")
    depth, quoted, escaped = 0, False, False
    for byte in raw:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted = True
        elif byte in {91, 123}:
            depth += 1
            require(depth <= 64, "JSON_DEPTH_LIMIT")
        elif byte in {93, 125}:
            depth -= 1

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            require(key not in result, "JSON_DUPLICATE_KEY")
            result[key] = value
        return result

    def nonfinite(value: str) -> Any:
        raise ConfirmationReportInvalid("JSON_NONFINITE")

    def finite_float(value: str) -> float:
        number = float(value)
        require(math.isfinite(number), "JSON_NONFINITE")
        return number

    try:
        return json.loads(
            raw, object_pairs_hook=pairs, parse_constant=nonfinite, parse_float=finite_float
        )
    except ConfirmationReportInvalid:
        raise
    except (ValueError, UnicodeError, RecursionError):
        raise ConfirmationReportInvalid("JSON_INVALID") from None


def review_bytes(raw: bytes) -> dict[str, Any]:
    """Bind the same bounded buffer that was parsed/reviewed; no second file read."""
    result = review(load(raw))
    result.update(
        receipt_version=RECEIPT_VERSION,
        reader_policy=READER_POLICY,
        input_sha256=hashlib.sha256(raw).hexdigest(),
        input_bytes=len(raw),
        binding_kind="RAW_INPUT_BYTES_SHA256",
        receipt_authenticity_verified=False,
    )
    return result


def verify_receipt(raw: bytes, receipt_raw: bytes) -> dict[str, Any]:
    require(len(receipt_raw) <= MAX_RECEIPT_BYTES, "RECEIPT_FILE_TOO_LARGE")
    try:
        receipt = load(receipt_raw)
    except ConfirmationReportInvalid as error:
        raise ConfirmationReportInvalid("RECEIPT_" + str(error)) from None
    require(isinstance(receipt, dict), "RECEIPT_OBJECT_REQUIRED")
    assert isinstance(receipt, dict)
    require(
        type(receipt.get("receipt_version")) is int
        and receipt["receipt_version"] == RECEIPT_VERSION,
        "RECEIPT_VERSION_INVALID",
    )
    require(receipt.get("reader_policy") == READER_POLICY, "RECEIPT_POLICY_MISMATCH")
    require(
        type(receipt.get("input_bytes")) is int
        and 0 <= receipt["input_bytes"] <= MAX_BYTES
        and isinstance(receipt.get("input_sha256"), str)
        and len(receipt["input_sha256"]) == 64
        and all(c in "0123456789abcdef" for c in receipt["input_sha256"]),
        "RECEIPT_BINDING_INVALID",
    )
    expected = review_bytes(raw)
    require(
        receipt["input_bytes"] == expected["input_bytes"]
        and receipt["input_sha256"] == expected["input_sha256"],
        "RECEIPT_INPUT_MISMATCH",
    )
    # Canonical JSON permits receipt key/whitespace changes, but does not equate
    # false with 0, or integer 1 with float 1.0, as Python dict equality would.
    require(
        json.dumps(receipt, sort_keys=True, separators=(",", ":"))
        == json.dumps(expected, sort_keys=True, separators=(",", ":")),
        "RECEIPT_RESULT_MISMATCH",
    )
    return dict(
        review="OFFLINE_SYNTHETIC_RECEIPT_COMPARISON_ONLY",
        receipt_matches_input=True,
        receipt_version=RECEIPT_VERSION,
        reader_policy=READER_POLICY,
        input_sha256=expected["input_sha256"],
        input_bytes=expected["input_bytes"],
        disposition=expected["disposition"],
        attention_required=expected["attention_required"],
        input_authenticity_verified=False,
        receipt_authenticity_verified=False,
        current_exchange_state_verified=False,
        financial_proof_reverified=False,
        runtime_admission_changed=False,
        execution_approved=False,
        next_action="KEEP_PAUSED_REVIEW_ONLY",
    )


def save_receipt(raw: bytes, destination: Path) -> dict[str, Any]:
    """Publish a complete new receipt without replacing any existing directory entry.

    A private temporary file is synced before an exclusive hard-link publication.
    No direct-write or replacing-rename fallback is allowed. Parent-directory or
    hardware crash durability is not certified by this operation.
    """
    receipt = review_bytes(raw)
    encoded = (json.dumps(receipt) + "\n").encode()
    require(len(encoded) <= MAX_RECEIPT_BYTES, "RECEIPT_FILE_TOO_LARGE")
    temporary: str | None = None
    fd: int | None = None
    attempted = confirmed = False
    failure: ReceiptStorageInvalid | None = None
    try:
        fd, temporary = tempfile.mkstemp(
            prefix=".runtime-review-", suffix=".tmp", dir=destination.parent
        )
        with os.fdopen(fd, "wb") as stream:
            fd = None  # The stream owns and closes it even on write/sync failure.
            written = stream.write(encoded)
            if written != len(encoded):
                raise OSError("incomplete receipt write")
            stream.flush()
            os.fsync(stream.fileno())
        attempted = True
        os.link(temporary, destination)  # Atomic no-replace publication, including symlink targets.
        confirmed = True
    except FileExistsError:
        failure = ReceiptStorageInvalid(
            "OUTPUT_ALREADY_EXISTS" if attempted else "OUTPUT_SAVE_FAILED",
            publication_attempted=attempted,
        )
    except OSError:
        failure = ReceiptStorageInvalid(
            "OUTPUT_SAVE_FAILED", publication_attempted=attempted, publication_confirmed=confirmed
        )
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                failure = ReceiptStorageInvalid(
                    "OUTPUT_SAVE_FAILED",
                    publication_attempted=attempted,
                    publication_confirmed=confirmed,
                )
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            except OSError:
                failure = ReceiptStorageInvalid(
                    "OUTPUT_TEMP_CLEANUP_FAILED",
                    publication_attempted=attempted,
                    publication_confirmed=confirmed,
                    cleanup_incomplete=True,
                )
    if failure is not None:
        # A publication syscall may have taken effect before reporting failure.
        # Never remove/rewrite the final destination to guess or roll it back.
        raise failure from None
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = ReportArgumentParser(
        description="Offline synthetic report consistency; never execution approval"
    )
    parser.add_argument("report", type=Path)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--verify-receipt", type=Path)
    modes.add_argument("--save-receipt", type=Path)
    try:
        args = parser.parse_args(argv)
        if args.save_receipt is not None and (
            os.path.normcase(os.path.abspath(args.save_receipt))
            == os.path.normcase(os.path.abspath(args.report))
        ):
            raise ReceiptStorageInvalid("OUTPUT_IS_INPUT")
        with args.report.open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        if args.save_receipt is not None:
            report = save_receipt(raw, args.save_receipt)
        elif args.verify_receipt is None:
            report = review_bytes(raw)
        else:
            with args.verify_receipt.open("rb") as stream:
                receipt_raw = stream.read(MAX_RECEIPT_BYTES + 1)
            report = verify_receipt(raw, receipt_raw)
    except ReceiptStorageInvalid as error:
        report = dict(
            review="RECEIPT_SAVE_FAILED",
            code=str(error),
            storage_publication_attempted=error.publication_attempted,
            storage_publication_confirmed=error.publication_confirmed,
            temporary_cleanup_incomplete=error.cleanup_incomplete,
            execution_approved=False,
            next_action="KEEP_PAUSED_REVIEW_ONLY",
        )
        status = 2
    except ConfirmationReportInvalid as error:
        report = dict(
            review="INVALID_REPORT",
            code=str(error),
            execution_approved=False,
            next_action="KEEP_PAUSED_REVIEW_ONLY",
        )
        status = 2
    except (OSError, ValueError, TypeError, RecursionError):
        report = dict(
            review="INVALID_REPORT",
            code="INPUT_INVALID",
            execution_approved=False,
            next_action="KEEP_PAUSED_REVIEW_ONLY",
        )
        status = 2
    else:
        status = 3 if report["attention_required"] else 0
    print(json.dumps(report))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
