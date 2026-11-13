"""Local receipt structure never becomes independent remote proof or authority."""

# ruff: noqa: F401,F811
import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from packages.domain.contracts import PositionState
from scripts import probe_demo_accounted_reads as common
from scripts import review_demo_historical_receipts as receipts
from scripts import review_demo_historical_requests as history
from services.execution.demo_models import CancelledExposureCorrection
from services.execution.readiness_closed_history import INCIDENT, RECEIPT
from tests.unit.test_historical_request_contract import ledger
from tests.unit.test_readiness_closed_history import NOW, basis


@pytest.fixture
def related(ledger):
    ledger.operations = {}
    ledger.request_no_send_evidence = {}
    ledger.cancelled_exposure_corrections = {}
    ledger.recovery_receipts = {}
    current = ledger.orders["2"]
    current.state = "CANCELED"
    current.exit_state = "UNKNOWN"
    original = current.model_copy(
        deep=True,
        update={
            "open_quantity": current.quantity,
            "actual_risk": Decimal(1),
            "position_state": PositionState.EXIT_PENDING,
        },
    )
    # Construct through the typed schema so enum/Decimal fields use production parsing.
    record = CancelledExposureCorrection.model_validate(
        dict(
            review_token="1" * 64,
            evidence_hash="2" * 64,
            exchange_fingerprint="3" * 64,
            corrected_at="2026-11-13T00:00:00Z",
            original_orders={"2": original.model_dump(mode="json")},
        )
    )
    ledger.cancelled_exposure_corrections["correction"] = record
    ledger.operations["correction"] = receipts.CORRECTION
    return ledger


def report(ledger):
    return receipts.relations(
        ledger, history.plan_requests(ledger, common), datetime(2026, 11, 13, 1, tzinfo=UTC)
    )


def test_matched_correction_and_shared_order_still_do_not_resolve(related):
    value = report(related)
    rows = {r["role"]: r for r in value["requests"] if r["role"] != "ENTRY"}
    for row in rows.values():
        correction = row["cancelled_exposure_correction_receipts"][0]
        assert correction["receipt_structure_matches_current_order"]
        assert (
            not correction["remote_proof_replayed"]
            and not correction["request_resolution_authorized"]
        )
        assert not row["remote_resolution_verified"] and not row["automatically_resolvable"]
    assert rows["STOP_LOSS"]["same_order_request_references"] == [
        rows["EXIT"]["request_reference_sha256"]
    ]
    assert not value["independent_exchange_proof_replayed"]


@pytest.mark.parametrize("change", ["marker", "token", "future", "original-fill", "current-exit"])
def test_incomplete_or_nonpermitted_receipt_changes_are_reported_as_mismatch(related, change):
    r = related.cancelled_exposure_corrections["correction"]
    if change == "marker":
        related.operations["correction"] = "OTHER"
    elif change == "token":
        r.review_token = "x"
    elif change == "future":
        r.corrected_at = datetime(2026, 11, 14, tzinfo=UTC)
    elif change == "original-fill":
        r.original_orders["2"].filled = 1
    else:
        related.orders["2"].exit_state = "FILLED"
    value = report(related)
    row = next(r for r in value["requests"] if r["role"] == "EXIT")
    assert not row["cancelled_exposure_correction_receipts"][0][
        "receipt_structure_matches_current_order"
    ]
    assert not row["remote_resolution_verified"]


def test_installed_manual_receipt_recognizer_does_not_prune_or_acknowledge(basis):
    _, ledger = basis
    o = ledger.orders[INCIDENT]
    item = dict(
        identifier=o.client_id,
        order=o,
        role="ENTRY",
        state="UNKNOWN",
        request_reference_sha256=history.ref(o.client_id),
    )
    before = ledger.model_dump_json()
    value = receipts.relations(ledger, [item], NOW)
    assert value["requests"][0]["manual_close_receipts"][0][
        "installed_receipt_structure_recognized"
    ]
    assert not value["requests"][0]["manual_close_receipts"][0]["request_resolution_authorized"]
    assert ledger.model_dump_json() == before and INCIDENT in ledger.orders


def test_non_ledger_payload_is_skipped_instead_of_implying_absent(related):
    item = history.plan_requests(related, common)[0]
    assert receipts.event_signature({"synthetic": "private"}, item, common) is None


@pytest.mark.parametrize("change", ["state", "no-send", "number"])
def test_unsafe_stored_field_values_do_not_get_printed_or_accepted(related, change):
    item = history.plan_requests(related, common)[0]
    body = dict(
        requests=dict(related.requests),
        orders={k: o.model_dump(mode="json") for k, o in related.orders.items()},
    )
    if change == "state":
        body["requests"][item["identifier"]] = "PRIVATE_UNRECOGNIZED"
    elif change == "no-send":
        body["request_no_send_evidence"] = {item["identifier"]: "PRIVATE_UNRECOGNIZED"}
    else:
        body["orders"]["0"]["filled"] = "NaN"
        body["orders"]["1"]["filled"] = "NaN"
    with pytest.raises(common.ProbeBlocked):
        receipts.event_signature(body, item, common)
