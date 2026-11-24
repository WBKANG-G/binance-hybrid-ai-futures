"""Explicit bounded consent cannot be replayed onto stale data or broader authority."""

import copy
import json
from pathlib import Path

import pytest

from services.execution.demo_conditional_source_binding import (
    ACCEPTANCE_SHA256,
    bind_fresh_source,
    verify_conditional_acceptance,
)
from services.execution.demo_source_reuse_review import SourceReuseBlocked
from tests.unit.test_source_reuse_review import fixture

ROOT = Path(__file__).resolve().parents[2]


def evidence():
    root = ROOT / "docs/reviews/20261124"
    return json.loads((root / "SOURCE_ACCEPTANCE_REVIEW.json").read_text()), json.loads(
        (root / "SOURCE_ACCEPTANCE_RECEIPT.json").read_text()
    )


def test_exact_user_instruction_is_recorded_with_source_only_scope():
    e, a = evidence()
    r = verify_conditional_acceptance(e, a)
    assert a["operator_reply"] == "다음 단계 진행해"
    assert a["acceptance_basis"] == "USER_DIRECTION_TO_PROCEED_IN_RESPONSE_TO_BOUND_SCOPE_QUESTION"
    assert r["source_acceptance_recorded"] and r["conditional_reuse_approved"]
    assert not r["original_database_write_authorized"] and not r["original_apply_ready"]


@pytest.mark.parametrize("day", ["2026-11-24", "2026-11-25", "2026-11-29"])
def test_fresh_matching_day_uses_existing_consent_but_grants_no_apply_authority(day):
    e, a = evidence()
    _, o, args = fixture(day)
    before = copy.deepcopy((e, a, o, args))
    r = bind_fresh_source(e, a, o, **args)
    assert r["source_accepted"] and r["source_acceptance_receipt_sha256"] == ACCEPTANCE_SHA256
    assert r["candidate_day_kst"] == day
    assert all(
        r[k] is False
        for k in (
            "apply_token_created",
            "original_apply_ready",
            "original_database_write_authorized",
            "request_resolution_authorized",
            "resend_authorized",
            "execution_approved",
            "orders_enabled",
            "live_trading_enabled",
        )
    )
    assert (e, a, o, args) == before


@pytest.mark.parametrize("fault", ["future_week", "stale", "amount", "orders", "gap"])
def test_prior_consent_does_not_override_evidence_gates(fault):
    e, a = evidence()
    _, o, args = fixture("2026-11-30" if fault == "future_week" else "2026-11-24")
    if fault == "stale":
        args["server_now_ms"] = o["observations_end_ms"] + 300001
    elif fault == "amount":
        o["summary"]["rounds"][0]["account"]["usdt_wallet"] = "5000.00000000"
    elif fault == "orders":
        o["summary"]["rounds"][1]["algo_orders"]["count"] = 1
    elif fault == "gap":
        o["summary"]["btc_trade_gap_rows"] = 1
    with pytest.raises(SourceReuseBlocked):
        bind_fresh_source(e, a, o, **args)


@pytest.mark.parametrize(
    "field,value",
    [
        ("original_database_write_authorized", True),
        ("conditional_reuse_approved", False),
        ("source_accepted", 1),
        ("operator_reply", "다른 답변"),
        ("candidate", {}),
        ("conditional_reuse_terms", {}),
    ],
)
def test_receipt_tamper_is_refused_against_fixed_pin(field, value):
    e, a = evidence()
    a[field] = value
    with pytest.raises(SourceReuseBlocked, match="SOURCE_BIND_ACCEPTANCE_MISMATCH"):
        verify_conditional_acceptance(e, a)


def test_rehashed_broader_proposal_cannot_replace_approved_terms():
    from services.execution.demo_source_acceptance import digest

    e, a = evidence()
    e["proposal"]["conditional_reuse"]["last_day_kst"] = "2026-12-31"
    e["proposal_sha256"] = digest(e["proposal"])
    a["proposal_sha256"] = e["proposal_sha256"]
    with pytest.raises(SourceReuseBlocked, match="SOURCE_BIND_PROPOSAL_MISMATCH"):
        verify_conditional_acceptance(e, a)
