"""Real PG/WSS failure flow; the uncertain observation is read-only and unresolved."""

# ruff: noqa: F811
import asyncio
import json
import os
from pathlib import Path
from urllib.parse import parse_qs

import pytest

from integrations.binance_usdm.rest import RESTResponse
from scripts.capture_demo_wss_hints import Capture
from services.execution.demo_tp_uncertain_cancel_candidate import PENDING_MARKER, VERIFIED_MARKER
from tests.fixtures.tp_candidate_session import observe
from tests.fixtures.tp_uncertain_cancel import uncertain_coordinator
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_opposite_tp import opposite_case  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_tp_trigger_check import case  # noqa: F401
from tests.integration.test_tp_admission_candidate import scenario


@pytest.mark.parametrize("resolve", [False, True])
def test_uncertain_cancel_is_not_retried_or_marked_success_without_full_proof(
    opposite_case, monkeypatch, capsys, resolve
):
    opposite_case[3]["trigger"] = False
    s, b, m, route, old, history = scenario(opposite_case, cap=100000, pages=10)
    store, prior, fake, options = s
    c = uncertain_coordinator(store, prior, b)
    s = (store, c, fake, options)
    sender = b.sender
    begin_native = c.begin_finalization
    confirm_native = c.confirm_flat
    probe_starts = []
    native_probe = c._probe_ticket

    def probe(ticket, source, *, observer=None):
        probe_starts.append(len(m.rows))
        return native_probe(ticket, source, observer=observer)

    monkeypatch.setattr(c, "_probe_ticket", probe)
    failed_ids = set()
    attempt = 0
    results = []
    delete_rows = []

    def begin():
        m.finalizing = True
        begin_native()

    def confirm():
        nonlocal attempt
        attempt += 1
        if resolve and attempt == 4:
            for identifier in failed_ids:
                fake.algos[identifier]["algoStatus"] = "CANCELED"
        value = confirm_native()
        results.append(value)
        return value

    def send(method, path, query, key):
        values = parse_qs(query.get_secret_value())
        identifier = values.get("clientAlgoId", [""])[0]
        before = fake.algos.get(identifier, {}).copy()
        response = sender(method, path, query, key)
        if (
            method == "DELETE"
            and path == "/fapi/v1/algoOrder"
            and before.get("orderType") == "STOP_MARKET"
            and before.get("algoStatus") == "NEW"
        ):
            fake.algos[identifier] = before
            failed_ids.add(identifier)
            delete_rows.append(len(m.rows))
            m.rows[-1]["status"] = 500
            return RESTResponse(500, b'{"code":-1007}', response.headers)
        return response

    monkeypatch.setattr(c, "begin_finalization", begin)
    monkeypatch.setattr(c, "confirm_flat", confirm)
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
    assert len(delete_rows) == 1 and sum(c.client.cancel_failures.values()) == 1
    assert c.uncertain_deferrals == (3 if resolve else 4)
    assert results == (["UNKNOWN"] * 3 + ["VERIFIED_FLAT"] if resolve else ["UNKNOWN"] * 4)
    assert report["flat_confirmation"] == ("VERIFIED_FLAT" if resolve else "UNKNOWN")
    assert report["tp_trigger_check"] == "NOT_ACCEPTED"
    assert report["new_entry_post_count"] == report["operator_exit_post_attempts"] == 1
    assert final.paused and not any(fake.positions.values())
    assert final.operations[c._marker()] == (VERIFIED_MARKER if resolve else PENDING_MARKER)
    assert final.reconciled is False  # Original final disconnect is never relaxed.
    assert all(final.orders[k] == v for k, v in history.items())
    assert report["history_and_baselines_preserved"]
    assert all(row["method"] == "GET" for row in m.rows[probe_starts[0] :])
    folder = Path(os.environ["TP_UNCERTAIN_FLOW_REPORT_DIR"])
    folder.mkdir(exist_ok=True, parents=True)
    (folder / ("resolve" + str(resolve) + ".json")).write_text(
        json.dumps(
            {
                "synthetic_only": True,
                "resolve": resolve,
                "uncertain_cancel": c.uncertain_cancel_diagnostics(),
                "result": report,
                "budget": b.diagnostics(),
                "trace": m.rows,
            },
            indent=2,
        )
        + "\n"
    )
