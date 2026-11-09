"""Prior handoff vs first-pending deferral; synthetic HTTP/DB/WSS only."""

# ruff: noqa: F811
import asyncio
import json
import os
from pathlib import Path
from urllib.parse import parse_qs

import pytest

from scripts.capture_demo_wss_hints import Capture
from services.execution import demo as demo_module
from tests.fixtures.tp_candidate_session import observe
from tests.fixtures.tp_fenced_handoff import handoff_coordinator
from tests.fixtures.tp_first_confirmation import first_coordinator
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_opposite_tp import opposite_case  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_tp_trigger_check import case  # noqa: F401
from tests.integration.test_tp_admission_candidate import scenario


@pytest.mark.parametrize("first_candidate", [False, True])
@pytest.mark.parametrize("pages,verify_on", [(1, 1), (10, 1), (1, 4), (10, 4), (10, 5)])
def test_first_confirmation_cost_and_mandatory_final_proof(
    opposite_case, monkeypatch, capsys, pages, verify_on, first_candidate
):
    opposite_case[3]["trigger"] = False
    s, budget, meter, route, old, history = scenario(opposite_case, cap=100000, pages=pages)
    store, c, fake, options = s
    c = (
        first_coordinator(store, c, budget)
        if first_candidate
        else handoff_coordinator(store, c, budget)
    )
    s = (store, c, fake, options)
    original_finalization = c.begin_finalization

    def begin_finalization():
        meter.finalizing = True
        original_finalization()

    monkeypatch.setattr(c, "begin_finalization", begin_finalization)
    source_send = budget.sender
    native_confirm = c.confirm_flat
    native_snapshot = demo_module.historical_snapshot
    attempt = 0
    attempts = []
    delayed_deletes = []
    snapshots = []
    pending_cancellations = set()

    def send(method, path, query, key):
        params = parse_qs(query.get_secret_value())
        identifier = params.get("clientAlgoId", [""])[0]
        prior = fake.algos.get(identifier, {}).copy()
        response = source_send(method, path, query, key)
        if (
            method == "DELETE"
            and path == "/fapi/v1/algoOrder"
            and prior.get("orderType") == "STOP_MARKET"
            and prior.get("algoStatus") == "NEW"
            and attempt < verify_on
        ):
            # ACK is not terminal proof: the next signed GET still reports NEW.
            fake.algos[identifier] = prior
            pending_cancellations.add(identifier)
            delayed_deletes.append({"attempt": attempt, "request_seq": len(meter.rows)})
        return response

    def snapshot(*args, **kwargs):
        snapshots.append({"attempt": attempt, "first_request_seq": len(meter.rows) + 1})
        return native_snapshot(*args, **kwargs)

    def confirm():
        nonlocal attempt
        attempt += 1
        if attempt >= verify_on:
            for identifier in pending_cancellations:
                fake.algos[identifier]["algoStatus"] = "CANCELED"
        first_seq = len(meter.rows) + 1
        result = native_confirm()
        attempts.append(
            {
                "attempt": attempt,
                "result": result,
                "first_request_seq": first_seq,
                "last_request_seq": len(meter.rows),
            }
        )
        return result

    budget.sender = send
    monkeypatch.setattr(c, "confirm_flat", confirm)
    monkeypatch.setattr(demo_module, "historical_snapshot", snapshot)
    before = store.read("DEMO-test")
    try:
        report = asyncio.run(
            observe(s, monkeypatch, profile="quiet", duration=0.8, capture=Capture())
        )
    finally:
        route.close()
        c.client.send = old
    capsys.readouterr()
    expected_results = (
        ["UNKNOWN"] * (verify_on - 1) + ["VERIFIED_FLAT"] if verify_on <= 4 else ["UNKNOWN"] * 4
    )
    assert [a["result"] for a in attempts] == expected_results
    expected_full = {1, verify_on}
    assert all(
        sum(row["attempt"] == n for row in snapshots)
        == (2 if first_candidate and n == 1 and verify_on > 1 else 5 if n in expected_full else 0)
        for n in range(1, min(verify_on, 4) + 1)
    )
    assert c.pending_confirmation_deferrals == (3 if verify_on > 4 else max(0, verify_on - 2))
    assert c.handoff_used == 1
    if first_candidate:
        assert c.first_pending_deferrals == (1 if verify_on > 1 else 0)
    assert budget.phase == "CLEANUP" and not budget.cleanup_overrun
    assert report["new_entry_post_count"] == report["operator_exit_post_attempts"] == 1
    assert (
        report["flat_confirmation"] == ("VERIFIED_FLAT" if verify_on <= 4 else "UNKNOWN")
        and report["tp_trigger_check"] == "NOT_ACCEPTED"
    )
    final = store.read("DEMO-test")
    assert final.paused and not any(fake.positions.values())
    if verify_on > 4:
        assert any(row["algoStatus"] == "NEW" for row in fake.algos.values())
        assert report["risk_state"] == "EMERGENCY"
    assert all(final.orders[k] == v for k, v in before.orders.items())
    assert report["history_and_baselines_preserved"] and all(r["status"] != 429 for r in meter.rows)
    first_seq = attempts[0]["first_request_seq"]
    confirmation = meter.rows[first_seq - 1 :]
    confirmation_weight = sum(row["modeled_request_weight"] for row in confirmation)
    get_weight = sum(
        row["modeled_request_weight"] for row in confirmation if row["method"] == "GET"
    )
    elapsed = confirmation[-1]["elapsed_ms"] - confirmation[0]["elapsed_ms"]
    assert elapsed < 60000
    assert sum(row["path"] == "/fapi/v1/income" for row in confirmation) >= pages * len(snapshots)
    if pages == 10 and verify_on == 4:
        if first_candidate:
            assert budget.weights["CLEANUP"] < 6000
        else:
            assert budget.weights["CLEANUP"] == 6341
    folder = Path(os.environ["TP_CLEANUP_ENVELOPE_REPORT_DIR"])
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"first{first_candidate}-pages{pages}-verify{verify_on}.json").write_text(
        json.dumps(
            {
                "synthetic_only": True,
                "first_confirmation_candidate": first_candidate,
                "handoff": c.handoff_diagnostics(),
                "first_confirmation": c.first_confirmation_diagnostics()
                if first_candidate
                else None,
                "pending_deferrals": c.pending_confirmation_deferrals,
                "synthetic_published_limit": 100000,
                "not_an_entry_at_6000": True,
                "income_pages": pages,
                "verification_attempts": attempts,
                "full_historical_snapshot_calls": snapshots,
                "delayed_delete_acknowledgements": delayed_deletes,
                "isolated_confirmation_first_seq": first_seq,
                "isolated_confirmation_weight": confirmation_weight,
                "isolated_confirmation_get_weight": get_weight,
                "isolated_confirmation_elapsed_ms": elapsed,
                "result": report,
                "budget": budget.diagnostics(),
                "trace": meter.rows,
            },
            indent=2,
        )
        + "\n"
    )
