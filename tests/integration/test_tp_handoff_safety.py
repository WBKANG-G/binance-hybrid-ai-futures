"""Full real PG/WSS pipeline; inject changes precisely between the two commands."""

# ruff: noqa: F811
import asyncio
import json
import os
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs
from uuid import uuid5

import pytest

from integrations.binance_usdm.rest import RESTResponse
from scripts.capture_demo_wss_hints import Capture
from tests.fixtures.tp_candidate_session import observe
from tests.fixtures.tp_fenced_handoff import handoff_coordinator
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_opposite_tp import opposite_case  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_tp_trigger_check import case  # noqa: F401
from tests.integration.test_tp_admission_candidate import scenario


@pytest.mark.parametrize(
    "fault",
    [
        "none",
        "local_fingerprint",
        "ttl",
        "clock_backward",
        "clock_nan",
        "remote_wallet",
        "remote_income",
        "snapshot_slow",
        "query500",
        "rate429",
        "incomplete_income",
        "exit_response_lost",
        "position_flat_before_exit",
        "fenced_owner",
    ],
)
def test_handoff_changes_failure_and_possible_send_never_duplicate_exit(
    opposite_case, monkeypatch, capsys, fault
):
    opposite_case[3]["trigger"] = False
    s, b, m, route, old, history = scenario(opposite_case, cap=100000)
    store, prior, fake, options = s
    c = handoff_coordinator(store, prior, b)
    s = (store, c, fake, options)
    native_begin, native_command = c.begin_finalization, c.command
    source_send = b.sender
    injected = False
    entry_lease = c.lease
    command_bounds = []

    def begin():
        m.finalizing = True
        native_begin()

    def command(operation, kind):
        if kind != "EMERGENCY_FLATTEN":
            return native_command(operation, kind)
        assert c._handoff is not None  # A full original PAUSE proof has completed.
        command_bounds.append(len(m.rows))
        if fault == "local_fingerprint":
            store.change(
                c.lease,
                "SYNTHETIC_HANDOFF_LOCAL_CHANGE",
                lambda ledger: setattr(ledger, "fingerprint", "changed"),
            )
        if fault in {"ttl", "clock_backward", "clock_nan"}:
            current = c.handoff_clock
            shift = 3 if fault == "ttl" else -3
            c.handoff_clock = (
                (lambda: float("nan")) if fault == "clock_nan" else lambda: current() + shift
            )
        if fault == "remote_wallet":
            fake.wallet += Decimal("1")
        if fault == "incomplete_income":
            m.pages, m.full = 10, True
        if fault == "fenced_owner":
            c.lease = c.lease.model_copy(update={"fence": c.lease.fence + 1})
        try:
            return native_command(operation, kind)
        finally:
            c.lease = entry_lease

    def send(method, path, query, key):
        nonlocal injected
        params = parse_qs(query.get_secret_value())
        checking = c.handoff_checks and not c.handoff_used and not c.handoff_fallbacks
        if (
            fault == "position_flat_before_exit"
            and c.handoff_used
            and method == "GET"
            and path == "/fapi/v3/positionRisk"
            and not injected
        ):
            own = store.read(c.lease.account_id).orders[str(c._own_operation)]
            fake.trigger(own.tp_algo_id)
            injected = True
        response = source_send(method, path, query, key)
        if (
            checking
            and not injected
            and path == "/fapi/v1/accountConfig"
            and fault in {"query500", "rate429", "snapshot_slow"}
        ):
            injected = True
            if fault == "snapshot_slow":
                current = c.handoff_clock
                c.handoff_clock = lambda: current() + 3
            else:
                status = 429 if fault == "rate429" else 500
                m.rows[-1]["status"] = status
                return RESTResponse(
                    status, b'{"code":-1}', response.headers | {"retry-after": "120"}
                )
        if fault == "remote_income" and c.handoff_checks and path == "/fapi/v1/income":
            injected = True
            return RESTResponse(
                200,
                b'[{"incomeType":"TRANSFER","asset":"USDT","tranId":"synthetic-transfer","income":"1"}]',
                response.headers,
            )
        if (
            fault == "exit_response_lost"
            and method == "POST"
            and path == "/fapi/v1/order"
            and params.get("reduceOnly") == ["true"]
        ):
            injected = True
            m.rows[-1]["status"] = 500
            return RESTResponse(500, b'{"code":-1007}', response.headers)
        return response

    b.sender = send
    monkeypatch.setattr(c, "begin_finalization", begin)
    monkeypatch.setattr(c, "command", command)
    try:
        report = asyncio.run(
            observe(s, monkeypatch, profile="quiet", duration=0.8, capture=Capture())
        )
        capsys.readouterr()
        before_repeat = len(m.rows)
        result = native_command(uuid5(c._own_operation, "finish"), "EMERGENCY_FLATTEN")
        assert result == "UNKNOWN" and len(m.rows) == before_repeat
    finally:
        route.close()
        c.client.send = old
    assert report["new_entry_post_count"] == 1 and report["operator_exit_post_attempts"] <= 1
    assert report["tp_trigger_check"] == "NOT_ACCEPTED" and report["final_paused"]
    assert all(store.read("DEMO-test").orders[k] == v for k, v in history.items())
    if fault in {"none", "exit_response_lost", "position_flat_before_exit"}:
        assert c.handoff_used == 1 and not c.handoff_fallbacks
    elif fault in {"rate429", "fenced_owner"}:
        assert not c.handoff_used and not c.handoff_fallbacks
    else:
        assert not c.handoff_used and c.handoff_fallbacks == 1
    if fault in {"rate429", "fenced_owner"}:
        assert (
            report["flat_confirmation"] == "UNKNOWN" and report["operator_exit_post_attempts"] == 0
        )
        assert any(fake.positions.values())
    elif fault == "incomplete_income":
        assert (
            report["flat_confirmation"] == "UNKNOWN" and report["operator_exit_post_attempts"] == 1
        )
    else:
        assert report["flat_confirmation"] == "VERIFIED_FLAT" and not any(fake.positions.values())
        assert report["operator_exit_post_attempts"] == (
            0 if fault == "position_flat_before_exit" else 1
        )
    if fault == "remote_income":
        assert store.read("DEMO-test").net_transfers == Decimal("1")
    if fault == "rate429":
        assert m.rows[-1]["status"] == 429 and c.client.cooldown_until > c.client.clock_ms()
    if fault == "fenced_owner":
        assert len(m.rows) == command_bounds[0]
    folder = Path(os.environ["TP_HANDOFF_SAFETY_REPORT_DIR"])
    folder.mkdir(parents=True, exist_ok=True)
    (folder / (fault + ".json")).write_text(
        json.dumps(
            {
                "synthetic_only": True,
                "fault": fault,
                "handoff": c.handoff_diagnostics(),
                "result": report,
                "trace": m.rows,
            },
            indent=2,
        )
        + "\n"
    )
