"""Bind approved source terms to fresh evidence, without any original-write authority."""

import hashlib

from services.execution.demo_source_acceptance import digest
from services.execution.demo_source_reuse_review import SourceReuseBlocked, review_eligibility

PROPOSAL_SHA256 = "f9bd837dff118698ad6be8bdd9ad38e5220bd330ae38ca5d12e1a240e4ec5143"
ACCEPTANCE_SHA256 = "a2bce7cf164c4671b55e453435f7df349b4cfeb5d2bc354161dd40ada55d2a75"


def require(ok, code):
    if not ok:
        raise SourceReuseBlocked(code)


def verify_conditional_acceptance(envelope, acceptance):
    """Trust the externally fixed review/consent pins, never self-reported hashes."""
    try:
        require(
            set(envelope) == {"review_id", "proposal_sha256", "proposal"},
            "SOURCE_BIND_ENVELOPE_INVALID",
        )
        p = envelope["proposal"]
        require(
            digest(p) == envelope["proposal_sha256"] == PROPOSAL_SHA256,
            "SOURCE_BIND_PROPOSAL_MISMATCH",
        )
        require(digest(acceptance) == ACCEPTANCE_SHA256, "SOURCE_BIND_ACCEPTANCE_MISMATCH")
        require(
            acceptance["policy"] == "DEMO_CONDITIONAL_SOURCE_ACCEPTANCE_V1"
            and acceptance["scope"] == "SOURCE_ONLY_EXACT_CANDIDATE_AND_BOUNDED_SAME_WEEK_REUSE"
            and acceptance["review_id"] == envelope["review_id"]
            and acceptance["proposal_sha256"] == PROPOSAL_SHA256
            and acceptance["candidate"] == p["candidate"]
            and acceptance["conditional_reuse_terms"] == p["conditional_reuse"]
            and acceptance["acknowledged_unverified_facts"] == p["unverified_facts"]
            and acceptance["source_accepted"] is True
            and acceptance["conditional_reuse_approved"] is True,
            "SOURCE_BIND_SCOPE_INVALID",
        )
        require(
            hashlib.sha256(acceptance["question_text"].encode()).hexdigest()
            == acceptance["question_text_sha256"],
            "SOURCE_BIND_QUESTION_MISMATCH",
        )
        for flag in (
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
            require(acceptance[flag] is False, "SOURCE_BIND_AUTHORITY_EXPANSION")
        return dict(
            source_acceptance_recorded=True,
            conditional_reuse_approved=True,
            source_acceptance_receipt_sha256=ACCEPTANCE_SHA256,
            original_database_write_authorized=False,
            original_apply_ready=False,
        )
    except SourceReuseBlocked:
        raise
    except (KeyError, TypeError, ValueError, OverflowError):
        raise SourceReuseBlocked("SOURCE_BIND_INPUT_INVALID") from None


def bind_fresh_source(
    envelope, acceptance, observation, *, verified_reference_start_ms, server_now_ms
):
    """Caller still must verify raw receipt hashes/identity and trusted server time.

    A valid source binding can be recorded for the observed day. It does not mint
    an apply token, clear unresolved requests, start services, or authorize writes.
    """
    verified = verify_conditional_acceptance(envelope, acceptance)
    evaluated = review_eligibility(
        envelope["proposal"],
        observation,
        verified_reference_start_ms=verified_reference_start_ms,
        server_now_ms=server_now_ms,
    )
    return (
        evaluated
        | verified
        | dict(
            source_accepted=True,
            apply_token_created=False,
            original_apply_ready=False,
            original_database_write_authorized=False,
            request_resolution_authorized=False,
            resend_authorized=False,
            execution_approved=False,
            orders_enabled=False,
            live_trading_enabled=False,
        )
    )
