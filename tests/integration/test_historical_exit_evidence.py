"""Fresh operation for the one never-dispatched EXIT; no repeat of earlier queries."""

# ruff: noqa: F401,F811
import json
from dataclasses import replace
from urllib.parse import parse_qs

import pytest

from packages.persistence.demo_order_quota import LocalQuotaStopped
from packages.persistence.demo_shared_weight import SharedWeightStopped
from scripts import probe_demo_accounted_reads as common
from scripts import review_demo_historical_requests as history
from scripts import review_demo_read_deployment as deployment
from tests.integration.test_historical_request_evidence import (
    adopted,
    anchored,
    base_installation,
    candidate,
    day12,
    day_sender,
    demo_system,
    execute,
    installation,
    invoke,
    prepare,
    ready,
    reviewed,
    run,
    sender,
    wire,
)
from tests.integration.test_runtime_read_accounting import financial
from tests.unit.test_quota_startup_read_only import files

pytestmark = pytest.mark.parametrize("installation", ["historical"], indirect=True)


@pytest.fixture
def predecessor(adopted):
    seen = []
    result = execute(adopted, sender=wire(adopted, seen, "sl_missing"), source=history.PRIOR_SOURCE)
    assert result["http_attempts"] == 5 and result["modeled_weight_charged"] == 9
    assert result["shared_total_weight"] == 277 and result["requests_unqueried"] == 1
    return adopted


def exit_wire(case, seen, mode="ok"):
    original = wire(case, seen, mode, base_weight=277)

    def send(method, path, query, key):
        if path not in {"/fapi/v1/time", "/fapi/v1/accountConfig"}:
            assert path == "/fapi/v1/order"
            assert parse_qs(query.get_secret_value())["origClientOrderId"] == ["synthetic-exit-2"]
        return original(method, path, query, key)

    return send


@pytest.mark.parametrize("mode", ["ok", "missing"])
def test_only_unsent_exit_is_observed_and_all_prior_evidence_preserved(predecessor, mode):
    before = financial(predecessor[0])
    disk = files(deployment.STORAGE.parent)
    seen = []
    result = execute(
        predecessor,
        exit_followup=True,
        sender=exit_wire(predecessor, seen, mode),
        source="exit-source",
    )
    assert seen == ["/fapi/v1/time", "/fapi/v1/accountConfig", "/fapi/v1/order"]
    assert result["http_attempts"] == 3 and result["modeled_weight_charged"] == 7
    assert result["audit_events_added"] == result["resource_checkpoints_added"] == 6
    assert (
        result["shared_total_weight"] == 284
        and result["requests_observed"] == 1
        and result["requests_unqueried"] == 0
    )
    assert (
        result["previous_historical_evidence_verified"]
        and result["evidence_scope"] == "PREVIOUSLY_UNQUERIED_EXIT_ONLY"
    )
    assert result["observations"][0]["role"] == "EXIT"
    assert result["observations"][0]["observed_status"] == (
        "NOT_FOUND_UNRESOLVED" if mode == "missing" else "FILLED"
    )
    assert not result["observations"][0]["remote_resolution_verified"]
    assert (
        result["request_states_unchanged"]
        and not result["execution_approved"]
        and not result["reconciliation_performed"]
    )
    assert financial(predecessor[0]) == before
    now = files(deployment.STORAGE.parent)
    active = next(deployment.STORAGE.glob("*.weight.sqlite3"))
    assert all(
        now[k] == v
        for k, v in disk.items()
        if k != str(active.relative_to(deployment.STORAGE.parent))
    )

    def forbidden():
        pytest.fail("status loaded credentials")

    status = execute(
        predecessor,
        exit_followup=True,
        status=True,
        source="exit-source",
        loader=forbidden,
        clock=replace(predecessor[3], boot=lambda: "changed"),
    )
    assert (
        status["http_attempts"]
        == status["modeled_weight_charged"]
        == status["audit_events_added"]
        == 0
    )
    assert status["original_attempt_summary"]["modeled_weight_charged"] == 7
    assert not status["credential_session_accessed"] and files(deployment.STORAGE.parent) == now
    with pytest.raises(common.ProbeBlocked, match="ALREADY_ATTEMPTED"):
        execute(
            predecessor,
            exit_followup=True,
            sender=exit_wire(predecessor, seen),
            source="exit-source",
        )
    assert len(seen) == 3


@pytest.mark.parametrize("prior_mode", ["ok", "missing"])
def test_other_predecessor_outcomes_cannot_authorize_a_followup(adopted, prior_mode):
    execute(adopted, sender=wire(adopted, [], prior_mode), source=history.PRIOR_SOURCE)
    before = (
        common.resource_rows(adopted[0][1].engine, adopted[0][0].scope),
        files(deployment.STORAGE.parent),
    )

    def forbidden():
        pytest.fail("credentials used before predecessor validation")

    with pytest.raises(common.ProbeBlocked, match="PARTIAL_PREDECESSOR_REQUIRED"):
        execute(adopted, exit_followup=True, loader=forbidden)
    assert before == (
        common.resource_rows(adopted[0][1].engine, adopted[0][0].scope),
        files(deployment.STORAGE.parent),
    )


def test_modified_prior_receipt_is_not_an_exit_authorization(predecessor):
    _, folder = history.paths(deployment.STORAGE, predecessor[0][4])
    p = folder / "complete.json"
    record = json.loads(p.read_text())
    record["observations"][0]["observed_status"] = "CANCELED"
    p.write_text(json.dumps(record))
    with pytest.raises(common.ProbeBlocked, match="PARTIAL_PREDECESSOR_REQUIRED"):
        execute(predecessor, exit_followup=True)


def test_previous_receipts_are_part_of_new_status_protected_snapshot(predecessor):
    execute(
        predecessor, exit_followup=True, sender=exit_wire(predecessor, []), source="exit-source"
    )
    _, folder = history.paths(deployment.STORAGE, predecessor[0][4])
    p = folder / "complete.json"
    p.write_bytes(p.read_bytes() + b" ")
    with pytest.raises(common.ProbeBlocked, match="PROTECTED_STATE_CHANGED"):
        execute(predecessor, exit_followup=True, status=True, source="exit-source")


def test_unknown_exit_outcome_never_resubmits(predecessor):
    seen = []
    state = {}
    before = financial(predecessor[0])
    with pytest.raises(LocalQuotaStopped):
        execute(
            predecessor,
            exit_followup=True,
            sender=exit_wire(predecessor, seen, "network"),
            state=state,
        )
    assert len(seen) == 3 and financial(predecessor[0]) == before
    with pytest.raises(common.ProbeBlocked, match="INCOMPLETE_ATTEMPT"):
        execute(predecessor, exit_followup=True, status=True)
    with pytest.raises(common.ProbeBlocked, match="ALREADY_ATTEMPTED"):
        execute(predecessor, exit_followup=True, sender=exit_wire(predecessor, seen))
    assert len(seen) == 3


def test_stale_clock_refuses_before_keys_or_new_intent(predecessor):
    before = files(deployment.STORAGE.parent)

    def forbidden():
        pytest.fail("credentials loaded before clock validation")

    with pytest.raises(SharedWeightStopped):
        execute(
            predecessor,
            exit_followup=True,
            clock=replace(predecessor[3], boot=lambda: "changed"),
            loader=forbidden,
        )
    assert files(deployment.STORAGE.parent) == before
