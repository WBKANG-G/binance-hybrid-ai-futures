"""Verify the exact bounded original-start reply; never grant logical write authority."""

import hashlib
import json

from services.execution.demo_source_acceptance import digest

REVIEW_SOURCE = "24b3fde9586902110ec8dc515bc82f35ef5e6d17"
REVIEW_RELEASE = "5ead533975d9a5baed27fc8c4d635248586d8e62"
REVIEW_ZIP_SHA256 = "863c1ca8e5a03a0fb537c6325a4b95a3c8e6e21ecfdd87c6829e24fb99180ebc"
PROPOSAL_SHA256 = "df6063c8f37613997a15ddd705184c141029a5ad1c197ececc3505f16854f10a"
QUESTION_SHA256 = "14602bf63210d0fda246092e28af458cc9920b72eb6a860bf614fa1a1f3b055e"
RECEIPT_SHA256 = "163e12d1d3431d61be751800dd44c17e8fe7091a23cb29231bb28c081fa424ac"


class InspectionAcceptanceBlocked(ValueError):
    pass


def require(ok, code):
    if not ok:
        raise InspectionAcceptanceBlocked(code)


def verify_inspection_acceptance(pending, proposal_body, receipt):
    """External pins bind this chat response to reviewed code and physical-start scope."""
    try:
        require(
            hashlib.sha256(proposal_body).hexdigest() == PROPOSAL_SHA256,
            "INSPECTION_PROPOSAL_MISMATCH",
        )
        p = json.loads(proposal_body)
        require(
            p["review_id"] == pending["review_id"] == "original-db-inspection-only-20261124-v1"
            and pending["proposal_file_sha256"] == PROPOSAL_SHA256
            and pending["reviewed_source_reference"] == REVIEW_SOURCE
            and pending["artifact_sha256"] == REVIEW_ZIP_SHA256,
            "INSPECTION_REVIEW_BINDING_MISMATCH",
        )
        require(
            hashlib.sha256(pending["question_text"].encode()).hexdigest()
            == pending["question_text_sha256"]
            == QUESTION_SHA256,
            "INSPECTION_QUESTION_MISMATCH",
        )
        require(digest(receipt) == RECEIPT_SHA256, "INSPECTION_RECEIPT_MISMATCH")
        require(
            receipt["policy"] == "ORIGINAL_DATABASE_INSPECTION_ONLY_ACCEPTANCE_V1"
            and receipt["scope"]
            == "ONE_NETWORKLESS_ORIGINAL_DB_START_TWO_READONLY_QUERIES_AND_CLEAN_STOP_ONLY"
            and receipt["review_id"] == pending["review_id"]
            and receipt["proposal_file_sha256"] == PROPOSAL_SHA256
            and receipt["reviewed_source_reference"] == REVIEW_SOURCE
            and receipt["reviewed_artifact_sha256"] == REVIEW_ZIP_SHA256
            and receipt["question_text"] == pending["question_text"]
            and receipt["question_text_sha256"] == QUESTION_SHA256,
            "INSPECTION_RECEIPT_SCOPE_MISMATCH",
        )
        require(
            receipt["operator_reply"] == "다음 단계 진행해줘"
            and receipt["acceptance_basis"]
            == "USER_DIRECTION_TO_PROCEED_IN_DIRECT_RESPONSE_TO_BOUND_ORIGINAL_INSPECTION_QUESTION"
            and receipt["exact_message_timestamp_available"] is False,
            "INSPECTION_REPLY_BINDING_MISMATCH",
        )
        require(
            digest({k: v for k, v in receipt.items() if k != "approval_id"})
            == receipt["approval_id"],
            "INSPECTION_APPROVAL_ID_MISMATCH",
        )
        for k in (
            "approval_received",
            "original_database_start_authorized",
            "postgresql_startup_shutdown_storage_writes_authorized",
            "single_attempt_only",
        ):
            require(receipt[k] is True, "INSPECTION_START_SCOPE_NOT_ACCEPTED")
        for k in (
            "original_ledger_write_authorized",
            "owner_lease_takeover_authorized",
            "baseline_adoption_authorized",
            "request_resolution_authorized",
            "resend_authorized",
            "execution_approved",
            "orders_enabled",
            "live_trading_enabled",
        ):
            require(receipt[k] is False, "INSPECTION_LOGICAL_AUTHORITY_EXPANDED")
        require(
            p["approval_received"] is False and p["execution_enabled"] is False,
            "INSPECTION_REVIEW_RECORD_CHANGED",
        )
        return dict(
            approval_id=receipt["approval_id"],
            proposal_file_sha256=PROPOSAL_SHA256,
            original_database_start_authorized=True,
            postgresql_startup_shutdown_storage_writes_authorized=True,
            original_ledger_write_authorized=False,
            original_apply_ready=False,
            order_writes=0,
            orders_enabled=False,
            live_trading_enabled=False,
        )
    except InspectionAcceptanceBlocked:
        raise
    except (KeyError, ValueError, TypeError, AttributeError):
        raise InspectionAcceptanceBlocked("INSPECTION_ACCEPTANCE_INPUT_INVALID") from None
