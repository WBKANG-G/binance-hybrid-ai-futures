"""Verify pinned chat-source consent metadata; no DB, network, tokens, or write authority."""

import hashlib
import json

ACCEPTED_PROPOSAL_SHA256 = "455f05eb38e26b8a495297ae867bf7667c2ed0330b285a8fa9d1c4ec68520da2"
ACCEPTED_RECEIPT_SHA256 = "2fd5c5591950b9ecd3d9d15c1feeae55115ed37898682d047450e1b4f8fd3a3f"


class SourceAcceptanceBlocked(ValueError):
    pass


def require(value, code):
    if not value:
        raise SourceAcceptanceBlocked(code)


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    ).hexdigest()


def verify_acceptance(envelope, receipt, *, trusted_proposal_sha256, trusted_receipt_sha256):
    """Pins must come from the trusted sealed artifact, not the supplied receipt."""
    require(
        set(envelope) == {"review_id", "proposal_sha256", "proposal"}, "SOURCE_ENVELOPE_INVALID"
    )
    proposal = envelope["proposal"]
    require(
        digest(proposal) == envelope["proposal_sha256"] == trusted_proposal_sha256,
        "SOURCE_PROPOSAL_MISMATCH",
    )
    require(digest(receipt) == trusted_receipt_sha256, "SOURCE_RECEIPT_MISMATCH")
    require(
        receipt["policy"] == "DEMO_PAUSED_PERIOD_SOURCE_ACCEPTANCE_V1"
        and receipt["scope"] == "SOURCE_ACCEPTANCE_ONLY"
        and receipt["review_id"] == envelope["review_id"]
        and receipt["proposal_sha256"] == trusted_proposal_sha256
        and receipt["candidate"] == proposal["candidate"]
        and receipt["source_accepted"] is True,
        "SOURCE_SCOPE_INVALID",
    )
    require(
        receipt["acknowledged_unverified_facts"] == proposal["unverified_facts"]
        and receipt["acceptance_basis"] == "EXPLICIT_USER_REPLY_TO_BOUND_SOURCE_QUESTION"
        and receipt["operator_reply"]
        and hashlib.sha256(receipt["question_text"].encode()).hexdigest()
        == receipt["question_text_sha256"],
        "SOURCE_ACKNOWLEDGEMENT_INVALID",
    )
    for name in (
        "original_database_write_authorized",
        "original_database_start_authorized",
        "original_owner_lease_takeover_authorized",
        "existing_guard_bypass_authorized",
        "request_resolution_authorized",
        "resend_authorized",
        "execution_approved",
        "orders_enabled",
        "live_trading_enabled",
    ):
        require(receipt[name] is False, "SOURCE_AUTHORITY_EXPANSION_FORBIDDEN")
    return dict(
        review_id=receipt["review_id"],
        source_acceptance_receipt_sha256=trusted_receipt_sha256,
        candidate=receipt["candidate"],
        source_accepted=True,
        original_database_write_authorized=False,
        original_apply_ready=False,
        execution_approved=False,
        orders_enabled=False,
        live_trading_enabled=False,
    )
