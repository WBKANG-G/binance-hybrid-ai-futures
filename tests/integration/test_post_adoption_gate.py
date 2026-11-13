"""Local gate reads native stores and reports clock freshness without recovery or sends."""

# ruff: noqa: F401,F811
import json
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import event, select, update
from sqlalchemy.exc import DBAPIError

from packages.persistence.demo import accounts
from packages.persistence.demo_order_quota import DurableOrderQuota, canonical
from scripts import adopt_demo_accounted_day13 as adoption
from scripts import probe_demo_accounted_reads as common
from scripts import review_demo_post_adoption as gate
from scripts import review_demo_read_code_install as fingerprint
from scripts import review_demo_read_deployment as deployment
from services.execution.demo_managed_order_quota import STORAGE_KEY
from tests.integration.test_accounted_day13_apply import reviewed, run
from tests.integration.test_accounted_day13_preview import day12, day_sender, prepare
from tests.integration.test_accounted_read_probe import (
    anchored,
    candidate,
    demo_system,
    invoke,
    ready,
    sender,
)
from tests.integration.test_accounted_read_probe import (
    installation as base_installation,
)
from tests.integration.test_runtime_read_accounting import financial
from tests.unit.test_quota_startup_read_only import files


@pytest.fixture
def installation(base_installation, request):
    a = base_installation[0]
    directory = deployment.STORAGE.parent / "order-quota"
    directory.mkdir(mode=0o700)
    quota = DurableOrderQuota.create(
        directory, "DEMO-test", uuid4(), monotonic=a[2].mono, wall_clock=a[2].utc
    )
    binding = canonical(
        dict(
            journal=str(quota.journal_id),
            policy=quota.policy.digest,
            scope="MANAGED_WRITERS_ONLY",
            external_usage="UNKNOWN",
        )
    )
    quota.close()
    with a[1].engine.begin() as c:
        value = c.execute(select(accounts.c.state)).scalar_one()
        value["operations"][STORAGE_KEY] = binding
        if getattr(request, "param", False):
            from packages.domain.contracts import OrderState, PositionState
            from services.execution.models import PaperOrder
            from tests.integration.test_paper import signal, snapshot

            for i in range(34):
                order = PaperOrder(
                    client_id=f"synthetic-entry-{i}",
                    signal=signal(environment="DEMO", purpose="DEMO_VALIDATION", strategy=None),
                    snapshot=snapshot(),
                    group="BTC",
                    quantity=".0007",
                    quantity_step=".0001",
                    price_tick=".1",
                    loss_per_unit="1",
                    budget="1",
                    remaining="0",
                    state=OrderState.FILLED,
                    position_state=PositionState.CLOSED,
                    algo_id=f"synthetic-sl-{i}",
                    tp_algo_id=f"synthetic-tp-{i}",
                )
                value["orders"][str(i)] = order.model_dump(mode="json")
            value["requests"] = {
                "synthetic-entry-0": "UNKNOWN",
                "synthetic-sl-1": "NOT_FOUND_UNRESOLVED",
                "synthetic-tp-2": "CANCEL_REQUESTED",
                "synthetic-unmatched": "SUBMITTING",
            }
            if request.param in {"historical", "local_receipts"}:
                value["orders"]["2"]["exit_id"] = "synthetic-exit-2"
                value["requests"] = {
                    "synthetic-entry-0": "UNKNOWN",
                    "synthetic-entry-1": "UNKNOWN",
                    "synthetic-sl-2": "UNKNOWN",
                    "synthetic-exit-2": "SUBMITTING",
                }
        if getattr(request, "param", None) == "local_receipts":
            from packages.persistence.demo import events
            from tests.fixtures.historical_receipts import seed

            for kind, stamp, payload in seed(value, a[2].utc()):
                c.execute(
                    events.insert().values(
                        event_id=str(uuid4()),
                        account_id="DEMO-test",
                        fence=0,
                        as_of=stamp,
                        kind=kind,
                        payload=json.dumps(payload),
                    )
                )
        c.execute(update(accounts).values(state=value))
    return base_installation


@pytest.fixture
def adopted(reviewed):
    run(reviewed, day_sender(reviewed, []), apply=True)
    return reviewed


def review(case, clock=None):
    a = case[0]
    evidence = json.loads(
        Path("docs/request_cost_evidence/PINNED_RUNTIME_COST_PROOF.json").read_text()
    )
    return gate.run(
        a[1].engine,
        deployment.STORAGE,
        a[0].scope,
        a[4],
        a[1].installation_id(),
        "DEMO-test",
        fingerprint,
        adoption,
        common,
        evidence,
        clocks=clock or case[3],
    )


def test_gate_verifies_real_order_store_and_preserves_all_state_without_credentials(
    adopted, monkeypatch
):
    from integrations.binance_usdm import demo
    from scripts import demo_key_session

    before = financial(adopted[0])
    disk = files(deployment.STORAGE.parent)
    resources = common.resource_rows(adopted[0][1].engine, adopted[0][0].scope)
    monkeypatch.setattr(
        demo_key_session, "credentials", lambda **kw: pytest.fail("credentials accessed")
    )
    monkeypatch.setattr(demo, "transport", lambda *a: pytest.fail("HTTP attempted"))
    result = review(adopted)
    assert result["post_adoption_local_gate"] == "REVIEW_COMPLETED_KEEP_PAUSED"
    assert result["local_storage_checks_passed"] and not result["local_blockers"]
    assert result["day13_adoption_integrity_verified"] and result["shared_audit_integrity_verified"]
    assert result["shared_cumulative_modeled_weight"] == 268 and result["shared_clock_valid"]
    assert (
        result["http_attempts"] == result["modeled_weight_charged"] == 0
        and not result["credential_session_accessed"]
    )
    assert (
        result["default_execution_denial_verified"]
        and result["pinned_cost_contract"]["documented_routes"] == 21
    )
    assert not result["accounted_order_usage_endpoint_supported"]
    assert (
        result["managed_quota_startup"]["slots_10s"] is None
        and result["managed_quota_startup"]["slots_60s"] is None
    )
    assert (
        "ORDER_USAGE_ENDPOINT_NOT_IN_ACCOUNTED_ROUTE_SET" in result["remaining_execution_blockers"]
    )
    assert (
        not result["execution_approved"]
        and not result["orders_enabled"]
        and not result["live_trading_enabled"]
    )
    assert not result["capacity_assumptions"]["request_cost_upper_bounds_proven"]
    assert financial(adopted[0]) == before and files(deployment.STORAGE.parent) == disk
    assert common.resource_rows(adopted[0][1].engine, adopted[0][0].scope) == resources
    assert review(adopted) == result


def test_clock_change_is_reported_as_freshness_blocker_without_recovery(adopted):
    before = common.resource_rows(adopted[0][1].engine, adopted[0][0].scope)
    disk = files(deployment.STORAGE.parent)
    result = review(adopted, replace(adopted[3], boot=lambda: "new-local-gate-boot"))
    assert result["day13_adoption_integrity_verified"] and result["shared_audit_integrity_verified"]
    assert not result["shared_clock_valid"] and result["shared_clock_reason"] == "BOOT_CHANGED"
    assert "SHARED_CLOCK_OR_BOOT_CHANGED" in result["local_blockers"]
    assert not result["journal_changed"] and result["http_attempts"] == 0
    assert common.resource_rows(adopted[0][1].engine, adopted[0][0].scope) == before
    assert files(deployment.STORAGE.parent) == disk


def test_corrupt_receipt_is_not_a_local_pass(adopted):
    op, _ = adoption.paths(deployment.STORAGE, adopted[0][4])
    with adopted[0][1].engine.begin() as c:
        value = c.execute(select(accounts.c.state)).scalar_one()
        value["attested_day_baselines"][op]["baseline_usdt"] = "4999"
        c.execute(update(accounts).values(state=value))
    with pytest.raises(common.ProbeBlocked, match="COMMIT_STATE_CHANGED"):
        review(adopted)


def test_sql_write_in_readonly_review_is_refused(adopted):
    engine = adopted[0][1].engine
    before = financial(adopted[0])

    def inject(c, cursor, statement, parameters, context, many):
        if statement.startswith("SELECT demo_accounts.account_id"):
            cursor.execute("UPDATE demo_accounts SET fence=fence+1")

    event.listen(engine, "before_cursor_execute", inject)
    try:
        with pytest.raises(DBAPIError):
            review(adopted)
    finally:
        event.remove(engine, "before_cursor_execute", inject)
    assert financial(adopted[0]) == before


def test_active_quota_writer_gets_blocker_and_no_credit(adopted):
    ledger = adopted[0][6].read("DEMO-test")
    identity = json.loads(ledger.operations[STORAGE_KEY])["journal"]
    from uuid import UUID

    writer = DurableOrderQuota(
        deployment.STORAGE.parent / "order-quota", "DEMO-test", UUID(identity)
    )
    try:
        result = review(adopted)
        assert not result["local_storage_checks_passed"]
        assert "QUOTA_MANAGED_WRITER_BUSY" in result["local_blockers"]
        assert not result["execution_approved"]
    finally:
        writer.close()


@pytest.mark.parametrize("change", ["weight", "missing", "duplicate"])
def test_cost_evidence_does_not_silently_change_model(change):
    evidence = json.loads(
        Path("docs/request_cost_evidence/PINNED_RUNTIME_COST_PROOF.json").read_text()
    )
    if change == "weight":
        evidence["routes"][0]["modeled_weight"] = 1
    if change == "missing":
        evidence["routes"].pop()
    if change == "duplicate":
        evidence["routes"][1] = evidence["routes"][0]
    with pytest.raises(common.ProbeBlocked):
        gate.cost_contract(evidence, common)


def test_clock_change_during_final_snapshot_is_included_in_result(adopted, monkeypatch):
    original = common.protected_snapshot
    count = 0

    def snapshot(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            adopted[0][2].wall_delta += 5
        return original(*args, **kwargs)

    monkeypatch.setattr(common, "protected_snapshot", snapshot)
    result = review(adopted)
    assert (
        not result["shared_clock_valid"] and result["shared_clock_reason"] == "WALL_MONOTONIC_DRIFT"
    )
    assert result["all_reviewed_state_unchanged"] and result["http_attempts"] == 0


@pytest.mark.parametrize("installation", [True], indirect=True)
def test_existing_34_closed_orders_and_four_requests_remain_unchanged(adopted):
    before = financial(adopted[0]), files(deployment.STORAGE.parent)
    result = review(adopted)
    assert result["day13_adoption_integrity_verified"] and result["all_reviewed_state_unchanged"]
    assert set(result["local_blockers"]) == {
        "HISTORICAL_UNRESOLVED_REQUESTS_REQUIRE_RECONCILIATION",
        "RUNTIME_PROOF_BATCH_CANNOT_FIT",
        "RUNTIME_ENTRY_BATCH_CANNOT_FIT",
    }
    report = result["historical_blocker_diagnostics"]
    assert report["unresolved_request_count"] == 4
    assert report["proof_capacity_deficit"] == 21666 and report["entry_capacity_deficit"] == 220
    assert all(not r["remote_resolution_verified"] for r in report["unresolved_requests"])
    assert before == (financial(adopted[0]), files(deployment.STORAGE.parent))
    assert result["http_attempts"] == 0 and not result["execution_approved"]
