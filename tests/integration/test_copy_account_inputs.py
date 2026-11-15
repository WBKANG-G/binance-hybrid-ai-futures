"""Pinned archived synthetic response; read-only content and input review."""

# ruff: noqa: F401,F811
import json
from datetime import timedelta

import pytest

from integrations.binance_usdm.rest import RESTResponse
from packages.persistence.demo_order_quota import LocalQuotaStopped
from services.execution.demo_copy_account_content import review_account_evidence_inputs
from services.execution.demo_evidence_snapshot import evidence_snapshot
from tests.integration.test_copy_baseline_evidence import stale_copy
from tests.integration.test_copy_response_archive import capture, fresh
from tests.integration.test_read_audit_bootstrap import ready
from tests.integration.test_response_evidence_bootstrap import setup_ready
from tests.integration.test_runtime_read_accounting import financial
from tests.integration.test_shared_weight_checkpoint import anchored, demo_system
from tests.unit.test_copy_account_content import account_body


@pytest.fixture
def archived(capture):
    a, journal, parent, sent, build = capture
    body = json.dumps(account_body()).encode()
    t = build(sender=lambda *args: RESTResponse(200, body, {"x-mbx-used-weight-1m": "100"}))
    t(*fresh(a))
    directory = parent / "one"
    pin = t.archive_report["receipt_sha256"]
    context = json.loads((directory / "intent.json").read_bytes())["credential_context_id"]
    now = a[2].utc()
    proposal = dict(
        account_receipt_sha256=pin,
        credential_context_id=context,
        stored_day_kst="2026-11-13",
        stored_week_kst="2026-11-09",
        target_day_kst="2026-11-15",
        history_start_utc="2026-11-12T15:00:00+00:00",
        history_end_utc=now.isoformat(),
        remote_identity_reference_sha256="1" * 64,
        history_reference_sha256="2" * 64,
        valuation_reference_sha256="3" * 64,
        valuation_kind="PROPOSED_RECONSTRUCTION",
        operator_statement_claimed=True,
        source_acceptance_claimed=True,
    )
    return a, journal, directory, pin, now, proposal


def test_no_proposal_does_not_infer_claims_or_source_acceptance(archived):
    a, journal, directory, pin, now, proposal = archived
    before = evidence_snapshot(a[1].engine, a[3])
    files = {p.name: p.read_bytes() for p in directory.iterdir()}
    fin = financial(a)
    result = review_account_evidence_inputs(journal, directory, pin, now=now)
    assert not result["proposal_input_shape_complete"] and not result["caller_claims_present"]
    assert (
        not result["operator_statement_independently_verified"]
        and len(result["remaining_requirements"]) == 8
    )
    assert financial(a) == fin and evidence_snapshot(a[1].engine, a[3]) == before
    assert {p.name: p.read_bytes() for p in directory.iterdir()} == files


def test_well_formed_unverified_references_never_become_accepted_proofs(archived):
    a, journal, directory, pin, now, proposal = archived
    result = review_account_evidence_inputs(journal, directory, pin, now=now, proposal=proposal)
    assert result["proposal_input_shape_complete"] and result["caller_claims_present"]
    for field in (
        "referenced_proofs_verified",
        "whole_history_coverage_verified",
        "midnight_valuation_verified",
        "stored_periods_verified_against_ledger",
        "remote_account_identity_verified",
        "baseline_source_accepted",
        "baseline_adoption_approved",
        "execution_approved",
        "orders_enabled",
    ):
        assert not result[field]


@pytest.mark.parametrize(
    "fault",
    [
        "extra",
        "missing",
        "pin",
        "context",
        "bad_hash",
        "target",
        "backwards",
        "week",
        "history_gap",
        "history_end_gap",
        "future_history",
        "valuation_kind",
        "statement_false",
        "statement_string",
        "accept_false",
        "accept_integer",
        "history_non_utc",
        "day_noncanonical",
    ],
)
def test_proposal_must_be_explicit_pinned_and_cover_interval(archived, fault):
    a, journal, directory, pin, now, p = archived
    if fault == "extra":
        p["execute"] = True
    if fault == "missing":
        del p["history_reference_sha256"]
    if fault == "pin":
        p["account_receipt_sha256"] = "0" * 64
    if fault == "context":
        p["credential_context_id"] = "00000000-0000-4000-8000-000000000000"
    if fault == "bad_hash":
        p["history_reference_sha256"] = "not-a-hash"
    if fault == "target":
        p["target_day_kst"] = "2026-11-14"
    if fault == "backwards":
        p["stored_day_kst"] = "2026-11-16"
    if fault == "week":
        p["stored_day_kst"] = "2026-11-08"
        p["stored_week_kst"] = "2026-11-02"
    if fault == "history_gap":
        p["history_start_utc"] = "2026-11-13T00:00:00+00:00"
    if fault == "history_end_gap":
        p["history_end_utc"] = (now - timedelta(seconds=1)).isoformat()
    if fault == "future_history":
        p["history_end_utc"] = (now + timedelta(seconds=1)).isoformat()
    if fault == "valuation_kind":
        p["valuation_kind"] = "CURRENT_WALLET_IS_MIDNIGHT"
    if fault == "statement_false":
        p["operator_statement_claimed"] = False
    if fault == "statement_string":
        p["operator_statement_claimed"] = "true"
    if fault == "accept_false":
        p["source_acceptance_claimed"] = False
    if fault == "accept_integer":
        p["source_acceptance_claimed"] = 1
    if fault == "history_non_utc":
        p["history_start_utc"] = "2026-11-13T00:00:00+09:00"
    if fault == "day_noncanonical":
        p["target_day_kst"] = "20261115"
    fin = financial(a)
    with pytest.raises(LocalQuotaStopped):
        review_account_evidence_inputs(journal, directory, pin, now=now, proposal=p)
    assert financial(a) == fin


@pytest.mark.parametrize("offset", [-1, 301, 86400])
def test_stale_future_or_next_day_archive_cannot_feed_current_review(archived, offset):
    a, journal, directory, pin, now, p = archived
    with pytest.raises(LocalQuotaStopped, match="FRESH_SAME_DAY"):
        review_account_evidence_inputs(journal, directory, pin, now=now + timedelta(seconds=offset))


def test_non_200_or_sparse_response_is_not_usable_content(capture):
    a, journal, parent, sent, build = capture
    t = build(sender=lambda *args: RESTResponse(201, b"{}", {"x-mbx-used-weight-1m": "100"}))
    t(*fresh(a))
    with pytest.raises(LocalQuotaStopped, match="HTTP_200_REQUIRED"):
        review_account_evidence_inputs(
            journal, parent / "one", t.archive_report["receipt_sha256"], now=a[2].utc()
        )
