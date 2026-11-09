"""Ticket invalidation, fresh global truth, SDK budgets and no hidden cancel retries."""

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
from services.execution.demo_tp_uncertain_cancel_candidate import PENDING_MARKER, VERIFIED_MARKER
from tests.fixtures.tp_candidate_session import observe
from tests.fixtures.tp_uncertain_cancel import uncertain_coordinator
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_opposite_tp import opposite_case  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_tp_trigger_check import case  # noqa: F401
from tests.integration.test_tp_admission_candidate import scenario


@pytest.mark.parametrize(
    "fault",
    [
        "none",
        "ttl",
        "backward_clock",
        "nan_clock",
        "local_fingerprint",
        "unpaused",
        "foreign_position",
        "foreign_regular",
        "foreign_algo",
        "mode_changed",
        "mismatched_algo",
        "query500",
        "rate429",
        "query_budget",
        "clock_budget",
        "time_budget",
        "fenced_probe",
        "cancel_applied_despite_error",
        "incomplete_final_income",
        "wallet_on_resolution",
    ],
)
def test_uncertain_ticket_cannot_establish_flat_or_bypass_new_truth(
    opposite_case, monkeypatch, capsys, fault
):
    opposite_case[3]["trigger"] = False
    s, b, m, route, old, history = scenario(opposite_case, cap=100000, pages=10)
    store, prior, fake, options = s
    c = uncertain_coordinator(store, prior, b)
    s = (store, c, fake, options)
    native_begin, native_confirm, native_probe = (
        c.begin_finalization,
        c.confirm_flat,
        c._probe_ticket,
    )
    sender = b.sender
    attempt = 0
    probing = False
    injected = False
    shared = []
    probe_bounds = []
    failed = set()
    sdk_cancel_count = []

    def begin():
        m.finalizing = True
        native_begin()

    def confirm():
        nonlocal attempt
        attempt += 1
        if attempt == 4 and fault in {
            "cancel_applied_despite_error",
            "incomplete_final_income",
            "wallet_on_resolution",
        }:
            for identifier in failed:
                fake.algos[identifier]["algoStatus"] = "CANCELED"
            if fault == "incomplete_final_income":
                m.full = True
            if fault == "wallet_on_resolution":
                fake.wallet += Decimal("1")
        return native_confirm()

    def probe(ticket, source, *, observer=None):
        nonlocal probing, injected
        lease = c.lease
        if observer is not None:
            assert observer is c.client and observer.source is source
            shared.append(observer.budget)
            if fault == "query_budget":
                observer.budget.signed_queries = MAX_SIGNED_QUERIES
            if fault == "clock_budget":
                observer.budget.clock_probes = MAX_CLOCK_PROBES
            if fault == "time_budget":
                observer.budget.started -= 41
        if (
            observer is None
            and not injected
            and fault
            in {
                "ttl",
                "backward_clock",
                "nan_clock",
                "local_fingerprint",
                "unpaused",
                "fenced_probe",
            }
        ):
            injected = True
            if fault in {"ttl", "backward_clock", "nan_clock"}:
                clock = c.handoff_clock
                c.handoff_clock = (
                    (lambda: float("nan"))
                    if fault == "nan_clock"
                    else lambda: clock() + (10 if fault == "ttl" else -10)
                )
            elif fault == "local_fingerprint":
                store.change(
                    c.lease,
                    "SYNTHETIC_LOCAL_CHANGE",
                    lambda ledger: setattr(ledger, "fingerprint", "changed"),
                )
            elif fault == "unpaused":
                store.change(
                    c.lease, "SYNTHETIC_UNPAUSED", lambda ledger: setattr(ledger, "paused", False)
                )
            else:
                c.lease = lease.model_copy(update={"fence": lease.fence + 1})
        probe_bounds.append(len(m.rows))
        probing = True
        try:
            return native_probe(ticket, source, observer=observer)
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
        ):
            fake.algos[identifier] = previous
            failed.add(identifier)
            sdk_cancel_count.append(len(m.rows))
            m.rows[-1]["status"] = 500
            return RESTResponse(500, b'{"code":-1007}', response.headers)
        if probing:
            if not injected and fault in {"query500", "rate429"}:
                injected = True
                status = 429 if fault == "rate429" else 500
                m.rows[-1]["status"] = status
                return RESTResponse(
                    status, b'{"code":-1}', response.headers | {"retry-after": "120"}
                )
            if path == "/fapi/v1/accountConfig" and fault == "mode_changed":
                return RESTResponse(
                    200, b'{"dualSidePosition":true,"multiAssetsMargin":false}', response.headers
                )
            if path == "/fapi/v1/openOrders" and fault == "foreign_regular":
                return RESTResponse(
                    200, b'[{"clientOrderId":"foreign","status":"NEW"}]', response.headers
                )
            if path == "/fapi/v1/openAlgoOrders" and fault == "foreign_algo":
                data = json.loads(response.body)
                row = data[0].copy()
                row["clientAlgoId"] = "foreign"
                data.append(row)
                return RESTResponse(200, json.dumps(data).encode(), response.headers)
            if path == "/fapi/v1/algoOrder" and fault == "mismatched_algo":
                data = json.loads(response.body)
                data["triggerPrice"] = "1"
                return RESTResponse(200, json.dumps(data).encode(), response.headers)
        return response

    monkeypatch.setattr(c, "begin_finalization", begin)
    monkeypatch.setattr(c, "confirm_flat", confirm)
    monkeypatch.setattr(c, "_probe_ticket", probe)
    b.sender = send
    try:
        report = asyncio.run(
            observe(s, monkeypatch, profile="quiet", duration=0.8, capture=Capture())
        )
    finally:
        route.close()
        c.client.send = old
    capsys.readouterr()
    final = store.read("DEMO-test")
    assert report["new_entry_post_count"] == report["operator_exit_post_attempts"] == 1
    assert final.paused
    assert report["tp_trigger_check"] == "NOT_ACCEPTED"
    assert all(final.orders[k] == v for k, v in history.items())
    assert not c._inside_full_confirmation and c._first_confirmation_source is None
    verified = fault in {"cancel_applied_despite_error", "wallet_on_resolution"}
    assert report["flat_confirmation"] == ("VERIFIED_FLAT" if verified else "UNKNOWN")
    if verified:
        assert final.operations[c._marker()] == VERIFIED_MARKER
        assert final.paused and len(sdk_cancel_count) == 1
    else:
        assert final.operations[c._marker()] == PENDING_MARKER
    if fault == "none":
        assert c.uncertain_deferrals == 4 and len(sdk_cancel_count) == 1
        assert all(row["method"] == "GET" for row in m.rows[probe_bounds[0] :])
    if fault in {"ttl", "backward_clock", "nan_clock", "local_fingerprint"}:
        assert (
            len(sdk_cancel_count) > 1
        )  # Original full proof ran again before another cancel intent.
    if fault in {"clock_budget", "time_budget"}:
        assert len(m.rows) == probe_bounds[0]
    if fault == "query_budget":
        assert len(m.rows) == probe_bounds[0] + 1
    if fault == "fenced_probe":
        assert len(m.rows) == probe_bounds[-1]
    if fault == "rate429":
        assert m.rows[-1]["status"] == 429 and c.client.cooldown_until > c.client.clock_ms()
    if fault == "foreign_position":
        assert fake.positions["ETHUSDT"] == Decimal("1")
    if fault == "wallet_on_resolution":
        assert final.value.equity == fake.wallet
    assert shared
    folder = Path(os.environ["TP_UNCERTAIN_SAFETY_REPORT_DIR"])
    folder.mkdir(exist_ok=True, parents=True)
    (folder / (fault + ".json")).write_text(
        json.dumps(
            {
                "synthetic_only": True,
                "fault": fault,
                "uncertain_cancel": c.uncertain_cancel_diagnostics(),
                "sdk_cancel_count": len(sdk_cancel_count),
                "result": report,
                "trace": m.rows,
            },
            indent=2,
        )
        + "\n"
    )
