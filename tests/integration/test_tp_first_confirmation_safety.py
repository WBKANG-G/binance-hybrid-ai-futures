"""First deferral must share proof budgets, preserve fencing and never claim flat."""

# ruff: noqa: F811
import asyncio
import json
import os
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs

import pytest

from integrations.binance_usdm.rest import RESTResponse
from scripts.capture_demo_wss_hints import Capture
from services.execution.demo_recovery_budget import MAX_CLOCK_PROBES, MAX_SIGNED_QUERIES
from tests.fixtures.tp_candidate_session import observe
from tests.fixtures.tp_first_confirmation import first_coordinator
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_opposite_tp import opposite_case  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_tp_trigger_check import case  # noqa: F401
from tests.integration.test_tp_admission_candidate import scenario


@pytest.mark.parametrize(
    "fault",
    [
        "none",
        "failed_delete",
        "unacknowledged",
        "malformed_positions",
        "wrong_algo_id",
        "unknown_status",
        "foreign_position",
        "local_unreconciled",
        "query500",
        "query404",
        "rate429",
        "query_budget",
        "clock_budget",
        "time_budget",
        "fenced_probe",
        "incomplete_final_income",
        "wallet_change_on_fourth",
        "foreign_on_fourth",
    ],
)
def test_first_deferral_cannot_bypass_original_final_proof_or_shared_budget(
    opposite_case, monkeypatch, capsys, fault
):
    opposite_case[3]["trigger"] = False
    s, b, m, route, old, history = scenario(opposite_case, cap=100000, pages=10)
    store, prior, fake, options = s
    c = first_coordinator(store, prior, b)
    source_client = c.client
    s = (store, c, fake, options)
    native_begin, native_confirm, native_probe = (
        c.begin_finalization,
        c.confirm_flat,
        c._pending_protection,
    )
    sender = b.sender
    attempt = 0
    probing = False
    injected = False
    pending = set()
    shared = []
    probe_bounds = []
    results = []

    def begin():
        m.finalizing = True
        native_begin()

    def confirm():
        nonlocal attempt
        attempt += 1
        if attempt == 4:
            if fault != "failed_delete":
                for identifier in pending:
                    fake.algos[identifier]["algoStatus"] = "CANCELED"
            if fault == "incomplete_final_income":
                m.full = True
            if fault == "wallet_change_on_fourth":
                fake.wallet += Decimal("1")
            if fault == "foreign_on_fourth":
                fake.positions["ETHUSDT"] = Decimal("1")
        result = native_confirm()
        results.append(result)
        return result

    def probe(source=None, *, observer=None):
        nonlocal probing
        if observer is None:
            return native_probe(source, observer=observer)
        assert observer is c.client and observer.source is source is source_client
        shared.append(observer.budget)
        probe_bounds.append(len(m.rows))
        lease = c.lease
        if fault == "query_budget":
            observer.budget.signed_queries = MAX_SIGNED_QUERIES
        if fault == "clock_budget":
            observer.budget.clock_probes = MAX_CLOCK_PROBES
        if fault == "time_budget":
            observer.budget.started -= 41
        if fault == "unacknowledged":
            source.acknowledged_cleanup_ids.clear()
        if fault == "local_unreconciled":
            store.change(
                c.lease,
                "SYNTHETIC_PROBE_LOCAL_CHANGE",
                lambda ledger: setattr(ledger, "reconciled", False),
            )
        if fault == "fenced_probe":
            c.lease = lease.model_copy(update={"fence": lease.fence + 1})
        probing = True
        try:
            return native_probe(source, observer=observer)
        finally:
            probing = False
            c.lease = lease

    def send(method, path, query, key):
        nonlocal injected
        values = parse_qs(query.get_secret_value())
        identifier = values.get("clientAlgoId", [""])[0]
        previous = fake.algos.get(identifier, {}).copy()
        if probing and fault == "foreign_position":
            fake.positions["ETHUSDT"] = Decimal("1")
        response = sender(method, path, query, key)
        if (
            method == "DELETE"
            and path == "/fapi/v1/algoOrder"
            and previous.get("orderType") == "STOP_MARKET"
            and previous.get("algoStatus") == "NEW"
            and (attempt < 4 or fault == "failed_delete")
        ):
            fake.algos[identifier] = previous
            pending.add(identifier)
            if fault == "failed_delete":
                m.rows[-1]["status"] = 500
                return RESTResponse(500, b'{"code":-1007}', response.headers)
        if probing and not injected:
            if fault in {"query500", "rate429"}:
                injected = True
                status = 429 if fault == "rate429" else 500
                m.rows[-1]["status"] = status
                return RESTResponse(
                    status, b'{"code":-1}', response.headers | {"retry-after": "120"}
                )
            if fault == "malformed_positions" and path == "/fapi/v3/positionRisk":
                injected = True
                return RESTResponse(200, b'[{"positionAmt":"0"}]', response.headers)
            if path == "/fapi/v1/algoOrder" and fault in {
                "wrong_algo_id",
                "unknown_status",
                "query404",
            }:
                injected = True
                if fault == "query404":
                    m.rows[-1]["status"] = 404
                    return RESTResponse(404, b'{"code":-2013}', response.headers)
                data = json.loads(response.body)
                data["clientAlgoId" if fault == "wrong_algo_id" else "algoStatus"] = "UNTRUSTED"
                return RESTResponse(200, json.dumps(data).encode(), response.headers)
        return response

    monkeypatch.setattr(c, "begin_finalization", begin)
    monkeypatch.setattr(c, "confirm_flat", confirm)
    monkeypatch.setattr(c, "_pending_protection", probe)
    b.sender = send
    try:
        report = asyncio.run(
            observe(s, monkeypatch, profile="quiet", duration=0.8, capture=Capture())
        )
        capsys.readouterr()
        assert c.client is source_client and c._first_confirmation_source is None
    finally:
        route.close()
        c.client.send = old
    assert report["new_entry_post_count"] == report["operator_exit_post_attempts"] == 1
    assert report["tp_trigger_check"] == "NOT_ACCEPTED" and report["final_paused"]
    assert all(store.read("DEMO-test").orders[k] == value for k, value in history.items())
    verified = fault in {
        "none",
        "unacknowledged",
        "malformed_positions",
        "wrong_algo_id",
        "unknown_status",
        "local_unreconciled",
        "wallet_change_on_fourth",
    }
    assert report["flat_confirmation"] == ("VERIFIED_FLAT" if verified else "UNKNOWN")
    early = fault in {
        "none",
        "incomplete_final_income",
        "wallet_change_on_fourth",
        "foreign_on_fourth",
    }
    assert c.first_pending_deferrals == (1 if early else 0)
    assert shared  # The first probe must use the actual full-confirmation budget.
    if fault in {"clock_budget", "time_budget", "fenced_probe"}:
        assert len(m.rows) == probe_bounds[0]
    if fault == "query_budget":
        assert len(m.rows) == probe_bounds[0] + 1  # Clock only; no signed request after exhaustion.
    if fault == "failed_delete":
        assert c.pending_confirmation_deferrals == 0 and all(
            result == "UNKNOWN" for result in results
        )
    if fault == "rate429":
        assert m.rows[-1]["status"] == 429 and c.client.cooldown_until > c.client.clock_ms()
    if fault == "wallet_change_on_fourth":
        assert store.read("DEMO-test").value.equity == fake.wallet
    if fault in {"foreign_position", "foreign_on_fourth"}:
        assert fake.positions["ETHUSDT"] == Decimal("1")
    folder = Path(os.environ["TP_FIRST_SAFETY_REPORT_DIR"])
    folder.mkdir(parents=True, exist_ok=True)
    (folder / (fault + ".json")).write_text(
        json.dumps(
            {
                "synthetic_only": True,
                "fault": fault,
                "first_confirmation": c.first_confirmation_diagnostics(),
                "attempt_results": results,
                "result": report,
                "trace": m.rows,
            },
            indent=2,
        )
        + "\n"
    )
