"""Installed runtime: synthetic HTTP/orders and local WSS only; no budget governor."""

# ruff: noqa: F811
import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from integrations.binance_usdm.demo_read_routing import TPReadRouting
from scripts.capture_demo_wss_hints import Capture, replay
from services.execution import demo as demo_module
from services.execution import demo_tp_trigger_check as module
from services.execution import demo_validation as validation
from tests.fixtures.installed_tp_budget_audit import COSTS, Meter, seed_closed_shape
from tests.fixtures.installed_tp_phase_audit import PhaseAudit
from tests.integration.test_demo import posts
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_opposite_tp import opposite_case  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_tp_trigger_check import case  # noqa: F401


async def observe(scenario, monkeypatch, *, profile, duration, capture):
    store, c, fake, options = scenario
    operation = uuid4()
    triggered = False
    sent_initial = False

    async def handler(socket):
        nonlocal triggered, sent_initial
        try:
            while True:
                if socket.close_code is not None:
                    return
                ledger = store.read("DEMO-test")
                order = ledger.orders.get(str(operation))
                just_triggered = False
                if (
                    not triggered
                    and options["trigger"]
                    and order
                    and ledger.validation_evidence.get(str(operation), {}).get("target_verified")
                ):
                    fake.trigger(order.tp_algo_id)
                    triggered = True
                    just_triggered = True
                if profile in {"burst", "pulse"} or not sent_initial or just_triggered:
                    await socket.send(json.dumps({"e": "ACCOUNT_UPDATE", "E": c.client.clock_ms()}))
                    sent_initial = True
                await asyncio.sleep(
                    0.02 if profile == "burst" else 0.25 if profile == "pulse" else 0.05
                )
        except Exception:
            pass

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]

        @asynccontextmanager
        async def connector(url):
            async with connect(f"ws://127.0.0.1:{port}") as socket:
                yield socket

        async def local_session(coordinator, *, on_ready):
            await capture.supervise(coordinator, connector=connector, on_ready=on_ready)

        monkeypatch.setattr(validation, "session", local_session)
        if duration != 300:
            monkeypatch.setattr(module, "OBSERVATION_SECONDS", duration)
        else:
            assert module.OBSERVATION_SECONDS == 300
        return await module.run(c, operation, entry_style="OPPOSITE_QUOTE")


def audit_case(
    opposite_case,
    monkeypatch,
    capsys,
    *,
    mode,
    profile,
    duration,
    pages=1,
    when="finalization",
    full=False,
    spike=False,
    latency=0.0,
):
    store, old, fake, options = opposite_case
    if mode != "tp":
        options["trigger"] = False
    if mode in {"partial", "unfilled"}:
        options["fill"] = False
    history = seed_closed_shape(store, old, fake)
    sender = old.client.send
    meter = Meter(
        sender,
        fake,
        partial=mode == "partial",
        spike=spike,
        pages=pages,
        when=when,
        full=full,
        latency=latency,
    )
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
    audit = PhaseAudit(route, COSTS)
    old.client.send = audit
    c = module.PooledReadTPTriggerCoordinator(store, old.lease, old.client)
    for name, phase in [
        ("connected", "SESSION_READY"),
        ("event", "OBSERVATION"),
        ("resume_entries", "RESUME"),
        ("reserve", "RESERVATION"),
        ("submit", "SUBMISSION_PROTECTION"),
        ("confirm_flat", "FLAT_CONFIRMATION"),
    ]:
        monkeypatch.setattr(c, name, audit.instrument(phase, getattr(c, name)))
    monkeypatch.setattr(validation, "prepare", audit.instrument("PREPARATION", validation.prepare))
    original_begin = validation.pause_finalization_locally

    def begin(coordinator):
        meter.finalizing = True
        audit.begin_finalization()
        return original_begin(coordinator)

    monkeypatch.setattr(validation, "pause_finalization_locally", begin)
    proofs = []
    historical = demo_module.historical_snapshot

    def counted(*a, **k):
        proofs.append(True)
        return historical(*a, **k)

    monkeypatch.setattr(demo_module, "historical_snapshot", counted)
    try:
        with audit.span("BOOTSTRAP"):
            c.client.sync_clock()
            c.refresh_metadata()
            c.reconcile()
        before = store.read("DEMO-test")
        assert before.reconciled and before.risk_state.value == "NORMAL", (
            before.reconciled,
            before.risk_state,
            before.reconciliation_failure_code,
        )
        capture = Capture()
        report = asyncio.run(
            observe(
                (store, c, fake, options),
                monkeypatch,
                profile=profile,
                duration=duration,
                capture=capture,
            )
        )
        n = len(meter.rows)
        try:
            replay_report = asyncio.run(replay(capture.hints))
        except Exception:
            replay_report = {"hint_replay": "NOT_VERIFIED_NO_AUTOMATIC_RETRY"}
        assert len(meter.rows) == n
    finally:
        route.close()
        c.client.send = sender
    output = capsys.readouterr().out
    completions = [
        json.loads(s)
        for s in output.splitlines()
        if s.startswith("{") and "completion_stage" in json.loads(s)
    ]
    assert completions, json.dumps(report)
    completion = completions[-1]
    after = store.read("DEMO-test")
    assert after.paused and report["history_and_baselines_preserved"]
    assert all(after.orders[k] == o for k, o in history.items())
    assert all(after.requests[k] == v for k, v in before.requests.items())
    assert (
        after.daily_base == before.daily_base
        and after.weekly_base == before.weekly_base
        and after.high_water == before.high_water
    )
    assert report["new_entry_post_count"] <= 1 and report["operator_exit_post_attempts"] <= 1
    assert not any(p["type"] == "MARKET" and p.get("reduceOnly") != "true" for p in posts(fake))
    assert not any(
        r["path"] in {"/fapi/v1/positionSide/dual", "/fapi/v1/multiAssetsMargin"}
        for r in meter.rows
    )
    result = audit.report()
    assert result["complete_cost_model"] and not result["trace_truncated"]
    assert result["attempts"] == len(meter.rows)
    rate = [r for r in meter.rows if r["status"] == 429]
    if rate:
        assert rate[0]["seq"] == len(meter.rows)
        assert (
            report["tp_trigger_check"] == "NOT_ACCEPTED"
            and c.client.cooldown_until > c.client.clock_ms()
        )
        if any(fake.positions.values()):
            assert report["flat_confirmation"] == "UNKNOWN" and report["risk_state"] == "EMERGENCY"
    elif full:
        assert (
            report["tp_trigger_check"] == "NOT_ACCEPTED"
            and report["flat_confirmation"] == "UNKNOWN"
        )
        assert completion["completion_failure"] != "NONE"
    else:
        assert not any(fake.positions.values()) and len(proofs) == 5
        if mode == "tp":
            assert (
                report["tp_trigger_check"] == "TP_FILLED_THEN_REMAINING_SL_CANCELED_FLAT_VERIFIED"
            )
    result.update(
        {
            "mode": mode,
            "event_profile": profile,
            "synthetic_http_latency_ms": int(latency * 1000),
            "observation_seconds": duration,
            "production_observation_seconds": 300,
            "observation_shortened_for_case": duration != 300,
            "elapsed_seconds": round(time.monotonic() - meter.started, 3),
            "income_pages": pages,
            "page10_full": full,
            "pagination_starts": when,
            "shared_ip_spike": spike,
            "capacity_1m": 6000,
            "per_minute_peaks": {str(k): v for k, v in meter.peaks.items()},
            "request_trace": meter.rows,
            "result": report,
            "completion": completion,
            "full_history_snapshot_count": len(proofs),
            "position_nonzero_at_end": any(fake.positions.values()),
            "captured_events": len(capture.hints),
            "replay_result": replay_report,
            "ip_model_mutation_costs_optimistically_zero": True,
            "order_rate_limits_verified": False,
            "shared_ip_capacity_reserved": False,
            "active_tp_admission_proven": False,
            "runtime_modified": False,
        }
    )
    folder = Path(os.environ["INSTALLED_TP_AUDIT_REPORT_DIR"])
    folder.mkdir(exist_ok=True, parents=True)
    name = f"{mode}-{profile}-{duration}-pages{pages}-{when}-full{full}-spike{spike}-latency{int(latency * 1000)}.json"
    (folder / name).write_text(json.dumps(result, indent=2) + "\n")
    return result


@pytest.mark.parametrize("mode", ["tp", "no_tp"])
def test_real_300_second_quiet_observation(opposite_case, monkeypatch, capsys, mode):
    r = audit_case(opposite_case, monkeypatch, capsys, mode=mode, profile="quiet", duration=300)
    assert not r["observation_shortened_for_case"]
    assert r["elapsed_seconds"] >= 300 and all(x <= 6000 for x in r["per_minute_peaks"].values())


@pytest.mark.parametrize("mode", ["no_tp", "partial", "unfilled"])
def test_full_duration_config_under_event_burst(opposite_case, monkeypatch, capsys, mode):
    r = audit_case(opposite_case, monkeypatch, capsys, mode=mode, profile="burst", duration=300)
    assert any(row["status"] == 429 for row in r["request_trace"])


@pytest.mark.parametrize(
    "pages,full,when",
    [
        (1, False, "finalization"),
        (3, False, "finalization"),
        (10, False, "finalization"),
        (10, True, "finalization"),
        (10, False, "preparation"),
    ],
)
def test_shortened_pagination_cleanup_stress(opposite_case, monkeypatch, capsys, pages, full, when):
    audit_case(
        opposite_case,
        monkeypatch,
        capsys,
        mode="no_tp",
        profile="quiet",
        duration=0.8,
        pages=pages,
        full=full,
        when=when,
    )


def test_shared_ip_spike_keeps_unknown_exposure_and_no_retry(opposite_case, monkeypatch, capsys):
    r = audit_case(
        opposite_case, monkeypatch, capsys, mode="no_tp", profile="quiet", duration=300, spike=True
    )
    assert r["position_nonzero_at_end"] and r["result"]["flat_confirmation"] == "UNKNOWN"


def test_modeled_50ms_http_and_four_hints_per_second(opposite_case, monkeypatch, capsys):
    r = audit_case(
        opposite_case,
        monkeypatch,
        capsys,
        mode="no_tp",
        profile="pulse",
        duration=300,
        latency=0.05,
    )
    assert r["synthetic_http_latency_ms"] == 50
    assert any(row["status"] == 429 for row in r["request_trace"])
