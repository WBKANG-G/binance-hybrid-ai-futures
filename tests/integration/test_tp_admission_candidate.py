"""Installed runtime plus opt-in candidate; all orders/weights are synthetic."""

# ruff: noqa: F811
import asyncio
import json
import os
from pathlib import Path

import pytest

from integrations.binance_usdm.demo_read_routing import TPReadRouting
from integrations.binance_usdm.demo_tp_admission import (
    TPAdmissionBudget,
    TPAdmissionStopped,
    TPModelProfile,
)
from scripts.capture_demo_wss_hints import Capture
from services.execution import demo as demo_module
from services.execution.demo_tp_admission_candidate import AdmissionTPTriggerCoordinator
from tests.fixtures.tp_admission_synthetic_exchange import COSTS, Meter, seed_closed_shape
from tests.fixtures.tp_candidate_session import observe
from tests.integration.test_demo import posts
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_opposite_tp import opposite_case  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_tp_trigger_check import case  # noqa: F401


def scenario(
    opposite_case,
    *,
    cap=6000,
    seed=0,
    partial=False,
    spike=False,
    pages=1,
    full=False,
    synthetic=True,
):
    store, old, fake, options = opposite_case
    history = seed_closed_shape(store, old, fake)
    original = old.client.send
    meter = Meter(
        original, fake, capacity=cap, partial=partial, spike=spike, pages=pages, full=full
    )
    meter.used[int(meter.now().timestamp()) // 60] = seed
    store.now = meter.now
    old.client.clock_ms = meter.clock_ms

    class Pool:
        def __call__(self, *a):
            return meter(*a)

        def close(self):
            pass

        def diagnostics(self):
            return {}

    route = TPReadRouting(meter, pool=Pool())
    costs = {key: max(40 if key[0] != "GET" else 1, value) for key, value in COSTS.items()}
    budget = TPAdmissionBudget(
        route, TPModelProfile(len(history)), costs, allow_synthetic_model_execution=synthetic
    )
    old.client.send = budget
    c = AdmissionTPTriggerCoordinator(store, old.lease, old.client, budget)
    original_finalization = c.begin_finalization

    def begin_finalization():
        meter.finalizing = True
        original_finalization()

    c.begin_finalization = begin_finalization
    return (store, c, fake, options), budget, meter, route, original, history


def save(name, budget, meter, report):
    folder = Path(os.environ["TP_ADMISSION_REPORT_DIR"])
    folder.mkdir(exist_ok=True, parents=True)
    (folder / (name + ".json")).write_text(
        json.dumps(
            {
                "synthetic_only": True,
                "candidate_cli_installed": False,
                "budget": budget.diagnostics(),
                "trace": meter.rows,
                "result": report,
            },
            indent=2,
        )
        + "\n"
    )


@pytest.mark.parametrize("cap,synthetic", [(6000, True), (100000, False)])
def test_before_session_gate_has_no_new_reservation_post_or_db_event(
    opposite_case, monkeypatch, cap, synthetic
):
    s, b, m, route, old, history = scenario(opposite_case, cap=cap, synthetic=synthetic)
    store, c, fake, _ = s
    before = store.read("DEMO-test")
    from sqlalchemy import select

    from packages.persistence.demo import accounts, events

    def rows():
        with store.engine.connect() as conn:
            return conn.execute(select(accounts)).mappings().all(), conn.execute(
                select(events)
            ).mappings().all()

    raw = rows()
    try:
        with pytest.raises(TPAdmissionStopped):
            asyncio.run(observe(s, monkeypatch, profile="burst", duration=0.8, capture=Capture()))
    finally:
        route.close()
        c.client.send = old
    assert raw == rows() and store.read("DEMO-test") == before and not posts(fake)
    assert not b.admitted and len(m.rows) == 2 and all(r["method"] == "GET" for r in m.rows)
    assert b.reason == (
        "MODEL_LIFECYCLE_DOES_NOT_FIT" if cap == 6000 else "UNPROVEN_MODEL_REAL_EXECUTION_DISABLED"
    )
    save(
        f"preflight-{cap}-synthetic{synthetic}",
        b,
        m,
        {"entry_posts": 0, "new_reservations": 0, "database_changed": False},
    )


@pytest.mark.parametrize(
    "mode,pages,full",
    [
        ("tp", 1, False),
        ("no_tp", 1, False),
        ("partial", 1, False),
        ("unfilled", 1, False),
        ("no_tp", 3, False),
        ("no_tp", 10, False),
        ("no_tp", 10, True),
    ],
)
def test_counterfactual_capacity_preserves_original_protection_and_full_cleanup(
    opposite_case, monkeypatch, capsys, mode, pages, full
):
    options = opposite_case[3]
    if mode != "tp":
        options["trigger"] = False
    if mode in {"partial", "unfilled"}:
        options["fill"] = False
    s, b, m, route, old, history = scenario(
        opposite_case, cap=100000, partial=mode == "partial", pages=pages, full=full
    )
    store, c, fake, _ = s
    before = store.read("DEMO-test")
    proofs = []
    original_snapshot = demo_module.historical_snapshot

    def count(*a, **k):
        proofs.append(True)
        return original_snapshot(*a, **k)

    monkeypatch.setattr(demo_module, "historical_snapshot", count)
    try:
        report = asyncio.run(
            observe(s, monkeypatch, profile="burst", duration=0.8, capture=Capture())
        )
    finally:
        route.close()
        c.client.send = old
    output = capsys.readouterr().out
    after = store.read("DEMO-test")
    assert (
        after.paused
        and report["history_and_baselines_preserved"]
        and all(after.orders[k] == v for k, v in before.orders.items())
    )
    assert (
        after.daily_base == before.daily_base
        and after.weekly_base == before.weekly_base
        and after.high_water == before.high_water
    )
    assert report["new_entry_post_count"] == 1 and report["operator_exit_post_attempts"] <= 1
    assert all(r["status"] != 429 for r in m.rows)
    assert b.phase == "CLEANUP" and not b.cleanup_overrun and max(m.peaks.values()) < 100000
    if full:
        assert (
            report["tp_trigger_check"] == "NOT_ACCEPTED"
            and report["flat_confirmation"] == "UNKNOWN"
        )
    else:
        assert (
            not any(fake.positions.values())
            and report["flat_confirmation"] == "VERIFIED_FLAT"
            and len(proofs) == 5
        )
        assert (
            report["tp_trigger_check"] == "TP_FILLED_THEN_REMAINING_SL_CANCELED_FLAT_VERIFIED"
        ) == (mode == "tp")
    assert "private-" not in output
    save(f"counterfactual-{mode}-pages{pages}-full{full}", b, m, report)


def test_observation_stop_keeps_cleanup_credit_and_private_key_close(
    opposite_case, monkeypatch, capsys
):
    opposite_case[3]["trigger"] = False
    s, b, m, route, old, _ = scenario(opposite_case, cap=100000, seed=60000, pages=10)
    store, c, fake, _ = s
    try:
        report = asyncio.run(
            observe(s, monkeypatch, profile="burst", duration=2, capture=Capture())
        )
    finally:
        route.close()
        c.client.send = old
    output = capsys.readouterr().out
    validations = [
        json.loads(line) for line in output.splitlines() if line.startswith('{"validation":')
    ]
    completions = [row for row in validations if "completion_failure" in row]
    assert len(completions) == 1
    completion = completions[0]
    assert completion["validation"] == "FINISHED_FLAT"
    assert completion["completion_failure"] == "NONE"
    report["original_validation_completion"] = completion
    assert b.stopped and b.reason == "ORDINARY_CLEANUP_RESERVE_REACHED"
    assert b.phase == "CLEANUP" and not b.cleanup_overrun and b.used < 100000
    assert report["new_entry_post_count"] == 1 and report["operator_exit_post_attempts"] == 1
    assert (
        report["tp_trigger_check"] == "NOT_ACCEPTED" and report["flat_confirmation"] == "UNKNOWN"
    )  # Original TP report remains conservative after any session failure.
    assert not any(fake.positions.values()) and report["risk_state"] == "EMERGENCY"
    assert any(r["method"] == "DELETE" and r["path"] == "/fapi/v1/listenKey" for r in m.rows)
    assert all(r["status"] != 429 for r in m.rows) and store.read("DEMO-test").paused
    save("counterfactual-ordinary-stop-cleanup-retained", b, m, report)


def test_shared_ip_spike_retains_sdk_cooldown_no_retry_and_unknown_exposure(
    opposite_case, monkeypatch, capsys
):
    opposite_case[3]["trigger"] = False
    s, b, m, route, old, _ = scenario(opposite_case, cap=100000, spike=True)
    store, c, fake, _ = s
    try:
        report = asyncio.run(
            observe(s, monkeypatch, profile="quiet", duration=2, capture=Capture())
        )
    finally:
        route.close()
        c.client.send = old
    capsys.readouterr()
    assert (
        any(fake.positions.values())
        and report["flat_confirmation"] == "UNKNOWN"
        and report["risk_state"] == "EMERGENCY"
    )
    assert m.rows[-1]["status"] == 429 and c.client.cooldown_until > c.client.clock_ms()
    assert report["operator_exit_post_attempts"] == 0 and report["new_entry_post_count"] == 1
    assert b.untrusted and report["tp_trigger_check"] == "NOT_ACCEPTED"
    save("counterfactual-shared-ip-spike", b, m, report)


@pytest.mark.parametrize("fault", ["missing_entry_header", "entry_allowance_overrun"])
def test_after_possible_entry_send_original_protection_finishes_before_local_stop(
    opposite_case, monkeypatch, capsys, fault
):
    from urllib.parse import parse_qs

    from integrations.binance_usdm.rest import RESTResponse

    opposite_case[3]["trigger"] = False
    s, b, m, route, old, _ = scenario(opposite_case, cap=100000)
    sender = b.sender
    injected = False

    def inject(method, path, query, key):
        nonlocal injected
        response = sender(method, path, query, key)
        params = parse_qs(query.get_secret_value())
        if (
            not injected
            and method == "POST"
            and path == "/fapi/v1/order"
            and params.get("type") == ["LIMIT"]
        ):
            injected = True
            if fault == "missing_entry_header":
                return RESTResponse(response.status, response.body, {})
            b.weights["ENTRY_PROTECTION"] = b.profile.entry + 1
        return response

    b.sender = inject
    store, c, fake, _ = s
    try:
        report = asyncio.run(
            observe(s, monkeypatch, profile="quiet", duration=0.8, capture=Capture())
        )
    finally:
        route.close()
        c.client.send = old
    output = capsys.readouterr().out
    final = store.read("DEMO-test")
    evidence = final.validation_evidence[report["operation_id"]]
    assert injected and evidence["stop_verified"] and evidence["target_verified"]
    assert b.stopped and b.phase == "CLEANUP" and not b.cleanup_overrun
    assert report["new_entry_post_count"] == report["operator_exit_post_attempts"] == 1
    assert report["tp_trigger_check"] == "NOT_ACCEPTED" and report["flat_confirmation"] == "UNKNOWN"
    assert (
        final.paused and final.risk_state.value == "EMERGENCY" and not any(fake.positions.values())
    )
    completions = [
        json.loads(line) for line in output.splitlines() if line.startswith('{"validation":')
    ]
    completion = next(row for row in completions if "completion_failure" in row)
    assert (
        completion["validation"] == "FINISHED_FLAT" and completion["completion_failure"] == "NONE"
    )
    assert all(r["status"] != 429 for r in m.rows)
    report["original_validation_completion"] = completion
    save("counterfactual-protection-" + fault, b, m, report)


def test_existing_operation_is_rejected_before_any_http_or_new_reservation(opposite_case):
    from uuid import uuid4, uuid5

    from services.execution.demo_tp_admission_candidate import run_admission_candidate

    s, b, m, route, old, _ = scenario(opposite_case, cap=100000)
    store, c, fake, _ = s
    operation = uuid4()
    marker = str(uuid5(operation, "tp-trigger-observation"))
    store.change(
        c.lease,
        "SYNTHETIC_PRIOR_OPERATION",
        lambda ledger: ledger.operations.__setitem__(marker, "TP_TRIGGER:NOT_ACCEPTED"),
    )
    before = store.read("DEMO-test")
    try:
        with pytest.raises(ValueError, match="must not be submitted again"):
            asyncio.run(run_admission_candidate(c, operation))
    finally:
        route.close()
        c.client.send = old
    assert store.read("DEMO-test") == before and not m.rows and not posts(fake)
