"""Explicit V2 offline policy: distinguish a failed trial from later reported cleanup."""

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from scripts import review_runtime_confirmation_report as v1
from scripts.review_tp_minute_budget import MAX_BYTES

READER_POLICY = "SYNTHETIC_SEPARATE_OUTCOMES_V2"
RECEIPT_VERSION = 2
TP_ACCEPTED = "TP_FILLED_THEN_REMAINING_SL_CANCELED_FLAT_VERIFIED"
FAILURES = frozenset(
    {
        "NONE",
        "TP_FILL_NOT_OBSERVED",
        "SL_CANCEL_NOT_VERIFIED",
        "SIGNED_PROOF_QUERY_FAILED",
        "NONZERO_POSITION_BEFORE_STOP_DELETE",
        "DEMO_RATE_LIMITED",
        "QUOTE_EXPIRED_DURING_ACCOUNT_CHECK",
        "CLOCK_STALE_OR_COOLDOWN",
        "LEASE_OR_RISK_GATE_REJECTED",
        "DEMO_SESSION_OR_QUERY_FAILED",
        "OTHER_VALIDATION_FAILURE",
    }
)


def require_receiver_split(data: dict[str, Any], d: dict[str, Any], result: dict[str, Any]) -> None:
    fault, diagnostic, wait = (
        data.get("receiver_fault"),
        result.get("private_session_diagnostic"),
        d.get("observation_resource_wait"),
    )
    v1.require(
        data.get("mode") == "receiver_failure"
        and isinstance(fault, dict)
        and set(fault) == {"deferred", "closed"}
        and fault["deferred"] is True
        and fault["closed"] is True
        and isinstance(diagnostic, dict)
        and diagnostic.get("stage") == "RECEIVE"
        and diagnostic.get("code") == "SOCKET_CLOSED"
        and d.get("session_failed") is True
        and d.get("quota_seal_failed") is False
        and d.get("bridge_degraded") is False
        and d.get("reported_outcome") == "PROOF_VERIFIED_SESSION_FAILED"
        and result.get("tp_trigger_check") == "NOT_ACCEPTED"
        and result.get("failure") == "DEMO_SESSION_OR_QUERY_FAILED"
        and result.get("history_and_baselines_preserved") is True
        and result.get("position_state") == "CLOSED"
        and data.get("runtime_budget_untrusted") is False
        and data.get("runtime_budget_stopped") is False
        and data.get("runtime_budget_reason") == "NONE",
        "DUAL_OUTCOME_RECEIVER_SCOPE_REQUIRED",
    )
    v1.require(isinstance(wait, dict), "DUAL_OUTCOME_WAIT_SCOPE_REQUIRED")
    assert isinstance(wait, dict)
    for key in ["start_deadline_seconds", "deferral_limit"]:
        v1.require(
            type(wait.get(key)) is int and wait[key] == 120, "DUAL_OUTCOME_WAIT_SCOPE_REQUIRED"
        )
    for key in [
        "full_reconciliation_omitted",
        "flat_watch_is_financial_proof",
        "execution_approved",
    ]:
        v1.require(wait.get(key) is False, "DUAL_OUTCOME_WAIT_SCOPE_REQUIRED")
    v1.require(v1.count(wait.get("deferrals")) >= 1, "DUAL_OUTCOME_WAIT_SCOPE_REQUIRED")
    v1.require(v1.count(data.get("unsealed_flow_count")) == 0, "DUAL_OUTCOME_UNSEALED")


def review(data: Any) -> dict[str, Any]:
    v1.require(isinstance(data, dict), "SYNTHETIC_REPORT_REQUIRED")
    assert isinstance(data, dict)
    d, result = data.get("confirmation_scheduler"), data.get("result")
    v1.require(isinstance(d, dict) and isinstance(result, dict), "CONFIRMATION_FIELDS_REQUIRED")
    assert isinstance(d, dict) and isinstance(result, dict)
    original, later = result.get("flat_confirmation"), d.get("returned_flat_confirmation")
    v1.require(
        isinstance(original, str) and original in {"UNKNOWN", "VERIFIED_FLAT"},
        "ORIGINAL_TRIAL_PROOF_STATUS_INVALID",
    )
    v1.require(
        isinstance(result.get("tp_trigger_check"), str)
        and result["tp_trigger_check"] in {TP_ACCEPTED, "NOT_ACCEPTED"}
        and isinstance(result.get("failure"), str)
        and result["failure"] in FAILURES,
        "ORIGINAL_TRIAL_FIELDS_REQUIRED",
    )
    accepted = result["tp_trigger_check"] == TP_ACCEPTED
    v1.require(
        not accepted or original == "VERIFIED_FLAT" and result["failure"] == "NONE",
        "ORIGINAL_TRIAL_ACCEPTANCE_MISMATCH",
    )
    split = original != later
    if split:
        v1.require(
            original == "UNKNOWN" and later == "VERIFIED_FLAT", "DUAL_OUTCOME_DIRECTION_INVALID"
        )
        require_receiver_split(data, d, result)
        # Validate every original V1 scheduler/trace/disabled-state invariant on
        # a private projection. Never change the input or label its trial successful.
        projection = dict(data)
        projection["result"] = dict(result, flat_confirmation=later)
        checked = v1.review(projection)
    else:
        checked = v1.review(data)
    disposition = checked["disposition"]
    if split and disposition == "REVIEW_REQUIRED_RUNTIME_FAILURE":
        disposition = "REVIEW_REQUIRED_FAILED_TRIAL_LATER_PROOF_REPORTED"
    elif not accepted and not checked["attention_required"]:
        disposition = "REVIEW_REQUIRED_ORIGINAL_TRIAL_NOT_ACCEPTED"
    checked.pop("returned_flat_confirmation")
    checked["later_reported_outcome"] = checked.pop("reported_outcome")
    checked.update(
        review="OFFLINE_SYNTHETIC_SEPARATE_OUTCOMES_CONSISTENCY_ONLY",
        version=2,
        disposition=disposition,
        attention_required=checked["attention_required"] or split or not accepted,
        original_trial_flat_confirmation=original,
        original_trial_tp_result=result["tp_trigger_check"],
        original_trial_failure=result["failure"],
        original_trial_reported_accepted=accepted,
        later_returned_flat_confirmation=later,
        outcomes_separated=split,
        separation_scope="SAVED_RECEIVER_DISCONNECT_ONLY" if split else "MATCHING_OUTCOMES",
        original_trial_reclassified=False,
    )
    return checked


def review_bytes(raw: bytes) -> dict[str, Any]:
    checked = review(v1.load(raw))
    checked.update(
        receipt_version=RECEIPT_VERSION,
        reader_policy=READER_POLICY,
        input_sha256=hashlib.sha256(raw).hexdigest(),
        input_bytes=len(raw),
        binding_kind="RAW_INPUT_BYTES_SHA256",
        receipt_authenticity_verified=False,
    )
    return checked


def verify_receipt(raw: bytes, receipt_raw: bytes) -> dict[str, Any]:
    v1.require(len(receipt_raw) <= v1.MAX_RECEIPT_BYTES, "RECEIPT_FILE_TOO_LARGE")
    receipt = v1.load(receipt_raw)
    v1.require(isinstance(receipt, dict), "RECEIPT_OBJECT_REQUIRED")
    assert isinstance(receipt, dict)
    v1.require(
        type(receipt.get("receipt_version")) is int
        and receipt["receipt_version"] == RECEIPT_VERSION,
        "RECEIPT_VERSION_INVALID",
    )
    v1.require(receipt.get("reader_policy") == READER_POLICY, "RECEIPT_POLICY_MISMATCH")
    expected = review_bytes(raw)
    v1.require(
        type(receipt.get("input_bytes")) is int
        and receipt["input_bytes"] == expected["input_bytes"]
        and receipt.get("input_sha256") == expected["input_sha256"],
        "RECEIPT_INPUT_MISMATCH",
    )
    v1.require(
        json.dumps(receipt, sort_keys=True, separators=(",", ":"))
        == json.dumps(expected, sort_keys=True, separators=(",", ":")),
        "RECEIPT_RESULT_MISMATCH",
    )
    return dict(
        review="OFFLINE_SYNTHETIC_V2_RECEIPT_COMPARISON_ONLY",
        receipt_version=RECEIPT_VERSION,
        reader_policy=READER_POLICY,
        receipt_matches_input=True,
        input_sha256=expected["input_sha256"],
        input_bytes=expected["input_bytes"],
        disposition=expected["disposition"],
        attention_required=expected["attention_required"],
        original_trial_flat_confirmation=expected["original_trial_flat_confirmation"],
        original_trial_reported_accepted=expected["original_trial_reported_accepted"],
        later_returned_flat_confirmation=expected["later_returned_flat_confirmation"],
        outcomes_separated=expected["outcomes_separated"],
        input_authenticity_verified=False,
        receipt_authenticity_verified=False,
        financial_proof_reverified=False,
        current_exchange_state_verified=False,
        runtime_admission_changed=False,
        execution_approved=False,
        next_action="KEEP_PAUSED_REVIEW_ONLY",
    )


def main(argv: list[str] | None = None) -> int:
    parser = v1.ReportArgumentParser(
        description="Explicit V2 offline outcome review, never approval"
    )
    parser.add_argument("report", type=Path)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--save-receipt", type=Path)
    modes.add_argument("--verify-receipt", type=Path)
    try:
        args = parser.parse_args(argv)
        if args.save_receipt is not None and os.path.normcase(
            os.path.abspath(args.save_receipt)
        ) == os.path.normcase(os.path.abspath(args.report)):
            raise v1.ReceiptStorageInvalid("OUTPUT_IS_INPUT")
        with args.report.open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        if args.verify_receipt is not None:
            with args.verify_receipt.open("rb") as stream:
                saved = stream.read(v1.MAX_RECEIPT_BYTES + 1)
            checked = verify_receipt(raw, saved)
        else:
            checked = review_bytes(raw)
            if args.save_receipt is not None:
                v1.publish_receipt(checked, args.save_receipt)
    except v1.ReceiptStorageInvalid as error:
        checked = dict(
            review="RECEIPT_SAVE_FAILED",
            code=str(error),
            storage_publication_attempted=error.publication_attempted,
            storage_publication_confirmed=error.publication_confirmed,
            temporary_cleanup_incomplete=error.cleanup_incomplete,
            execution_approved=False,
            next_action="KEEP_PAUSED_REVIEW_ONLY",
        )
        status = 2
    except v1.ConfirmationReportInvalid as error:
        checked = dict(
            review="INVALID_V2_REPORT",
            code=str(error),
            execution_approved=False,
            next_action="KEEP_PAUSED_REVIEW_ONLY",
        )
        status = 2
    except (OSError, ValueError, TypeError, RecursionError):
        checked = dict(
            review="INVALID_V2_REPORT",
            code="INPUT_INVALID",
            execution_approved=False,
            next_action="KEEP_PAUSED_REVIEW_ONLY",
        )
        status = 2
    else:
        status = 3 if checked["attention_required"] else 0
    print(json.dumps(checked))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
