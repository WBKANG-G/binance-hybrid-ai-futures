"""Unresolved history stays unresolved even when every local order is closed."""

import hashlib
from types import SimpleNamespace

import pytest

from packages.domain.contracts import OrderState, PositionState
from scripts.review_demo_post_adoption import historical_diagnostics


def history():
    orders = {}
    for i in range(34):
        orders[str(i)] = SimpleNamespace(
            client_id=f"entry-{i}",
            algo_id=f"sl-{i}",
            tp_algo_id=f"tp-{i}",
            exit_id=f"exit-{i}",
            state=OrderState.FILLED,
            position_state=PositionState.CLOSED,
            remaining=0,
            open_quantity=0,
        )
    return SimpleNamespace(
        orders=orders,
        requests={
            "entry-0": "UNKNOWN",
            "sl-1": "NOT_FOUND_UNRESOLVED",
            "tp-2": "CANCEL_REQUESTED",
            "unmatched-private-id": "SUBMITTING",
            "exit-3": "FILLED",
        },
        request_no_send_evidence={"entry-0": "LOCAL_VALIDATION_FAILED"},
    )


def test_real_history_count_reproduces_both_deficits_without_pruning():
    ledger = history()
    report = historical_diagnostics(ledger)
    assert report["unresolved_request_count"] == 4
    assert report["historical_orders_retained"] == 34
    assert report["proof_capacity_deficit"] == 21666
    assert report["entry_capacity_deficit"] == 220
    assert not report["capacity_fits_unchanged_model"]
    proof = report["proof_components"]
    assert (
        proof["snapshots_weight"]
        + proof["original_id_query_weight"]
        + proof["fixed_weight"]
        + proof["reserve"]
        == 27366
    )
    entry = report["entry_components"]
    assert entry["snapshots_weight"] + entry["fixed_weight"] + entry["reserve"] == 5920
    assert report["model_assumptions"]["max_income_pages"] == 10
    assert report["model_assumptions"]["flat_attempts"] == 4
    assert report["historical_rows_omitted"] == 0
    assert not report["history_pruning_permitted"] and not report["budget_increase_permitted"]
    assert not report["reconciliation_performed"] and not report["execution_approved"]


def test_closed_local_order_and_no_send_tag_never_clear_remote_uncertainty():
    ledger = history()
    report = historical_diagnostics(ledger)
    rows = {r["request_reference_sha256"]: r for r in report["unresolved_requests"]}
    row = rows[hashlib.sha256(b"entry-0").hexdigest()]
    assert row["local_no_send_evidence"] == "LOCAL_VALIDATION_FAILED"
    assert row["matched_local_orders"][0]["local_exposure_zero"]
    assert row["matched_local_orders"][0]["role"] == "ENTRY"
    assert not row["remote_resolution_verified"] and not row["automatically_resolvable"]
    assert "unmatched-private-id" not in str(report)
    assert "entry-0" not in str(report)
    assert ledger.requests["entry-0"] == "UNKNOWN"
    assert not rows[hashlib.sha256(b"unmatched-private-id").hexdigest()]["matched_local_orders"]
    assert historical_diagnostics(ledger) == report


@pytest.mark.parametrize("count", [0, 1, 6, 34, 128])
def test_capacity_blockers_derive_from_whole_history(count):
    ledger = history()
    template = next(iter(ledger.orders.values()))
    ledger.orders = {str(i): template for i in range(count)}
    report = historical_diagnostics(ledger)
    assert report["historical_orders_retained"] == count
    assert report["capacity_fits_unchanged_model"] == (count <= 1)
