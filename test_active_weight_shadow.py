"""Synthetic weighted lifecycle stress. Never loads real keys or calls a venue."""

import asyncio
import json
import os
import threading
import time
from collections import Counter
from contextlib import asynccontextmanager
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path
from urllib.parse import parse_qs
from uuid import uuid4

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from integrations.binance_usdm.demo_read_routing import TPReadRouting
from integrations.binance_usdm.demo_snapshot import operational_snapshot
from integrations.binance_usdm.rest import RESTResponse
from packages.domain.contracts import OrderState, PositionState, RiskState
from services.execution import demo_tp_trigger_check as tp_module
from services.execution import demo_validation as validation
from services.execution.demo_stream import session
from services.execution.demo_tp_trigger_check import (
    PooledReadTPTriggerCoordinator,
)
from services.execution.models import PaperOrder
from tests.integration.test_demo import posts
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_opposite_tp import exercise_opposite, opposite_case  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_tp_trigger_check import case  # noqa: F401
from tests.integration.test_paper import signal, snapshot

ROOT = Path(__file__).parent
REFERENCE = json.loads((ROOT / "observed_reference.json").read_text())
GET_COSTS = {path: values[1] for path, values in REFERENCE["get_observed_delta_ranges"].items()}


def seed_history(store, c, fake):
    history = {}
    for n in range(19):
        manual = n == 16
        ambiguous = n == 17
        unfilled = n == 18
        cid = f"DE-shadow-history-{n}"
        exit_id = None if manual or unfilled else f"DX-shadow-history-{n}"
        stop = None if unfilled else f"DS-shadow-history-{n}"
        target = None if ambiguous or unfilled else f"DT-shadow-history-{n}"
        qty = D("0.0007")
        filled = D(0) if ambiguous or unfilled else qty
        sig = signal(
            environment="DEMO",
            purpose="DEMO_VALIDATION",
            strategy=None,
            signal_closed_candle_at=store.now() - timedelta(days=2),
            expires_at=store.now() - timedelta(days=2) + timedelta(seconds=10),
            suggested_limit_price="80000.1",
            initial_stop_price="79600",
            target_price="80800.2",
        )
        key = str(sig.signal_id)
        order = PaperOrder(
            client_id=cid,
            signal=sig,
            snapshot=snapshot(),
            group="BTC",
            quantity=qty,
            quantity_step="0.0001",
            price_tick="0.1",
            loss_per_unit="500",
            budget="1",
            state=OrderState.CANCELED if not filled else OrderState.FILLED,
            position_state=PositionState.CLOSED,
            filled=filled,
            remaining="0",
            open_quantity="0",
            average_price="80000.1" if filled else "0",
            server_order_id=str(1000 + n),
            algo_id=stop,
            tp_algo_id=target,
            exit_id=exit_id,
            exit_state="NONE" if manual or unfilled else "UNKNOWN" if ambiguous else "FILLED",
        )
        history[key] = order
        raw = dict(
            symbol="BTCUSDT",
            clientOrderId=cid,
            orderId=str(1000 + n),
            side="BUY",
            type="LIMIT",
            status=order.state.value,
            origQty=str(qty),
            executedQty=str(filled),
            avgPrice=str(order.average_price),
            price="80000.1",
            positionSide="BOTH",
            reduceOnly=False,
        )
        fake.orders[cid] = raw
        if exit_id and not ambiguous:
            fake.orders[exit_id] = dict(
                raw,
                clientOrderId=exit_id,
                orderId=str(2000 + n),
                side="SELL",
                type="MARKET",
                status="FILLED",
                reduceOnly=True,
                price="0",
            )
        for identifier, kind, price in (
            (stop, "STOP_MARKET", "79600"),
            (target, "TAKE_PROFIT_MARKET", "80800.2"),
        ):
            if identifier:
                fake.algos[identifier] = dict(
                    symbol="BTCUSDT",
                    clientAlgoId=identifier,
                    algoId=str(3000 + 2 * n + (kind == "TAKE_PROFIT_MARKET")),
                    side="SELL",
                    orderType=kind,
                    algoStatus="EXPIRED",
                    triggerPrice=price,
                    closePosition=True,
                    positionSide="BOTH",
                    workingType="MARK_PRICE",
                    priceProtect=False,
                    algoType="CONDITIONAL",
                    actualOrderId="",
                )
        if ambiguous:
            fake.algos.pop(stop)
    # Two additional historical no-send uncertainties: source status remains preserved.
    fake.algos.pop("DS-shadow-history-0")
    fake.algos.pop("DT-shadow-history-1")

    def bind(ledger):
        ledger.orders.update(history)
        ledger.paused = True
        ledger.stream_ready = ledger.reconciled = False
        for order in history.values():
            ledger.requests[order.client_id] = order.state.value
            if order.exit_id:
                ledger.requests[order.exit_id] = (
                    "UNKNOWN" if order.exit_state == "UNKNOWN" else "FILLED"
                )
            for identifier in (order.algo_id, order.tp_algo_id):
                if identifier:
                    ledger.requests[identifier] = (
                        "UNKNOWN" if identifier not in fake.algos else "VERIFIED"
                    )

    store.change(c.lease, "SYNTHETIC_WEIGHT_SHADOW_HISTORY", bind)
    return history


class Meter:
    def __init__(
        self,
        sender,
        fake,
        *,
        seed,
        unknown_cost,
        cap,
        spike,
        partial,
        options,
        dual_cost=35,
        windowed=False,
    ):
        self.sender, self.fake = sender, fake
        self.used = seed
        self.seed = seed
        self.unknown_cost = unknown_cost
        self.get_costs = dict(GET_COSTS)
        self.get_costs["/fapi/v1/positionSide/dual"] = dual_cost
        self.cap = cap
        self.spike = spike
        self.spiked = False
        self.partial = partial
        self.options = options
        self.windowed = windowed
        self.window = int(fake.at.timestamp()) // 60
        self.window_origin = self.window
        self.window_peaks = {self.window: seed}
        self.rows = []
        self.lock = threading.Lock()
        self.started = time.monotonic()

    def __call__(self, method, path, query, key):
        params = {k: v[0] for k, v in parse_qs(query.get_secret_value()).items()}
        protected = (
            any(self.fake.positions.values())
            and any(
                a["algoStatus"] == "NEW" and a["orderType"] == "STOP_MARKET"
                for a in tuple(self.fake.algos.values())
            )
            and any(
                a["algoStatus"] == "NEW" and a["orderType"] == "TAKE_PROFIT_MARKET"
                for a in tuple(self.fake.algos.values())
            )
        )
        if method == "POST" and path == "/fapi/v1/order":
            stage = "reduce_exit" if params.get("reduceOnly") == "true" else "entry"
        elif method == "POST" and path == "/fapi/v1/algoOrder":
            stage = "protection"
        elif method == "DELETE":
            stage = "cancel_or_cleanup"
        elif protected:
            stage = "protected_reads"
        elif any(self.fake.positions.values()):
            stage = "unprotected_reads"
        elif any(
            o["status"] == "FILLED"
            and o["clientOrderId"].startswith("DE-")
            and "shadow-history" not in o["clientOrderId"]
            for o in tuple(self.fake.orders.values())
        ):
            stage = "post_fill_flat_reads"
        else:
            stage = "pre_entry_reads"
        cost = (
            self.get_costs[path]
            if method == "GET" and path in self.get_costs
            else self.unknown_cost
        )
        with self.lock:
            window = int(self.fake.at.timestamp()) // 60
            if self.windowed and window != self.window:
                self.window = window
                self.used = 0
            if (
                self.spike
                and not self.spiked
                and protected
                and method == "GET"
                and path == "/fapi/v1/time"
            ):
                self.used = max(self.used, self.cap)
                self.spiked = True
            self.used += cost
            self.window_peaks[self.window] = max(self.window_peaks.get(self.window, 0), self.used)
            blocked = self.used > self.cap
            row = {
                "seq": len(self.rows) + 1,
                "method": method,
                "path": path,
                "stage": stage,
                "assumed_weight": cost,
                "simulated_used_weight": self.used,
                "forwarded": not blocked,
                "status": 429 if blocked else None,
                "elapsed_ms": int((time.monotonic() - self.started) * 1000),
            }
            self.rows.append(row)
        if blocked:
            return RESTResponse(
                429, b"not-json", {"X-MBX-USED-WEIGHT-1M": str(self.used), "Retry-After": "120"}
            )
        response = self.sender(method, path, query, key)
        if (
            self.partial
            and method == "POST"
            and path == "/fapi/v1/order"
            and params.get("type") == "LIMIT"
        ):
            self.fake.fill(params["newClientOrderId"], D("0.0003"), D("80000.1"))
        row["status"] = response.status
        return RESTResponse(
            response.status,
            response.body,
            {"X-MBX-USED-WEIGHT-1M": str(row["simulated_used_weight"])},
        )


async def quiet_exercise(scenario, monkeypatch):
    store, c, fake, options = scenario
    operation = uuid4()
    sent_entry = sent_protection = triggered = False

    async def handler(socket):
        nonlocal sent_entry, sent_protection, triggered
        try:
            while socket.close_code is None:
                ledger = store.read("DEMO-test")
                order = ledger.orders.get(str(operation))
                if order and order.filled and not sent_entry:
                    await socket.send(
                        json.dumps({"e": "ORDER_TRADE_UPDATE", "E": c.client.clock_ms()})
                    )
                    sent_entry = True
                if order and order.protected and order.tp_algo_id:
                    if not sent_protection:
                        await socket.send(
                            json.dumps({"e": "ALGO_UPDATE", "E": c.client.clock_ms()})
                        )
                        sent_protection = True
                    if (
                        options["trigger"]
                        and not triggered
                        and ledger.validation_evidence.get(str(operation), {}).get(
                            "target_verified"
                        )
                    ):
                        await asyncio.sleep(0.05)
                        fake.trigger(order.tp_algo_id)
                        await socket.send(
                            json.dumps({"e": "ALGO_UPDATE", "E": c.client.clock_ms()})
                        )
                        triggered = True
                await asyncio.sleep(0.02)
        except Exception:
            pass

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]

        @asynccontextmanager
        async def connector(url):
            async with connect(f"ws://127.0.0.1:{port}") as socket:
                yield socket

        async def local_session(coordinator, *, on_ready):
            await session(coordinator, connector=connector, on_ready=on_ready)

        monkeypatch.setattr(validation, "session", local_session)
        monkeypatch.setattr(tp_module, "OBSERVATION_SECONDS", 0.8)
        return await tp_module.run(c, operation, entry_style="OPPOSITE_QUOTE")


SCENARIOS = (
    [(mode, 0, 6000, False) for mode in ["tp", "unfilled", "no_tp", "partial", "sl"]]
    + [(mode, 5000, 6000, False) for mode in ["tp", "no_tp"]]
    + [("spike", 0, 6000, True)]
    + [(mode, 0, 10**9, False) for mode in ["tp", "no_tp"]]
)


SCENARIOS += [(mode, 0, 6000, False) for mode in ["quiet_tp", "quiet_no_tp"]]
SCENARIOS += [(mode, 0, 10**9, False) for mode in ["quiet_tp", "quiet_no_tp"]]


@pytest.mark.parametrize("dual_cost", [30, 35])
@pytest.mark.parametrize("unknown_cost", [1, 40])
@pytest.mark.parametrize("mode,seed,cap,spike", SCENARIOS)
def test_active_weight_lifecycle_shadow(
    opposite_case,  # noqa: F811
    monkeypatch,
    mode,
    seed,
    cap,
    spike,
    unknown_cost,  # noqa: F811
    dual_cost,
):  # noqa: F811
    store, old, fake, options = opposite_case
    seed_history(store, old, fake)
    original = old.client.send
    base_mode = mode.removeprefix("quiet_")
    if base_mode == "unfilled":
        options.update(fill=False, trigger=False)
    elif base_mode in {"no_tp", "partial", "spike"}:
        options["trigger"] = False
    elif base_mode == "sl":
        options["trigger"] = "SL"
    if base_mode == "partial":
        options["fill"] = False
    meter = Meter(
        original,
        fake,
        seed=seed,
        unknown_cost=unknown_cost,
        cap=cap,
        spike=spike,
        partial=base_mode == "partial",
        options=options,
        dual_cost=dual_cost,
    )

    class Pool:
        def __call__(self, *args):
            return meter(*args)

        def close(self):
            pass

        def diagnostics(self):
            return {"audit_transport_mode": "POOLED_HTTPS", "audit_http_attempts": len(meter.rows)}

    route = TPReadRouting(meter, pool=Pool())
    old.client.send = route
    c = PooledReadTPTriggerCoordinator(store, old.lease, old.client)
    before = store.read("DEMO-test")
    report = {}
    setup_blocked = False
    try:
        c.client.settings = c.client.settings.model_copy(update={"demo_trading_enabled": False})
        c.client.sync_clock()
        c.refresh_metadata()
        c.reconcile()
        c.client.settings = c.client.settings.model_copy(update={"demo_trading_enabled": True})
        report = (
            asyncio.run(quiet_exercise((store, c, fake, options), monkeypatch))
            if mode.startswith("quiet_")
            else exercise_opposite((store, c, fake, options), monkeypatch)
        )
    except Exception as error:
        from integrations.binance_usdm.demo import DemoFailure

        assert isinstance(error, DemoFailure), type(error)
        setup_blocked = True
    finally:
        route.close()
        c.client.send = original
    after = store.read("DEMO-test")
    entries = [p for p in posts(fake) if p.get("reduceOnly") != "true"]
    exits = [p for p in posts(fake) if p.get("reduceOnly") == "true"]
    assert len(entries) <= 1 and len(exits) <= 1
    assert all(
        p["type"] == "LIMIT"
        and p["symbol"] == "BTCUSDT"
        and p["side"] == "BUY"
        and D(p["quantity"]) <= D("0.0007")
        and D(p["quantity"]) * D(p["price"]) <= 100
        for p in entries
    )
    assert after.paused
    for key, order in before.orders.items():
        assert after.orders[key] == order
    assert all(after.requests[k] == value for k, value in before.requests.items())
    assert after.daily_base == before.daily_base and after.weekly_base == before.weekly_base
    if report.get("flat_confirmation") == "VERIFIED_FLAT":
        assert not any(fake.positions.values()) and all(
            not o.open_quantity and not o.remaining for o in after.orders.values()
        )
    limited = any(r["status"] == 429 for r in meter.rows)
    if limited and any(fake.positions.values()):
        assert (
            report.get("flat_confirmation") != "VERIFIED_FLAT"
            and after.risk_state == RiskState.EMERGENCY
        )
    if spike:
        assert meter.spiked and limited and any(fake.positions.values())
        assert report.get("flat_confirmation") == "UNKNOWN" and not exits
    if cap > 6000 and base_mode == "tp":
        assert (
            report.get("tp_trigger_check") == "TP_FILLED_THEN_REMAINING_SL_CANCELED_FLAT_VERIFIED"
        ), report
    if cap > 6000 and base_mode == "no_tp":
        assert report.get("flat_confirmation") == "VERIFIED_FLAT" and len(exits) == 1, report
    if base_mode != "tp":
        assert (
            report.get("tp_trigger_check") != "TP_FILLED_THEN_REMAINING_SL_CANCELED_FLAT_VERIFIED"
        )
    phases = Counter()
    for row in meter.rows:
        phases[row["stage"]] += row["assumed_weight"]
    result = {
        "mode": mode,
        "hint_schedule": "state_change_only" if mode.startswith("quiet_") else "20ms_hint_stress",
        "initial_model_weight": seed,
        "model_limit": cap,
        "unmeasured_route_assumption": unknown_cost,
        "dual_side_assumed_delta": dual_cost,
        "read_weight_model": "observed-delta sensitivity with explicit dual-side override; not endpoint proof",
        "history_records": len(before.orders),
        "http_attempts": len(meter.rows),
        "modeled_request_weight": sum(r["assumed_weight"] for r in meter.rows),
        "model_peak_used_weight": meter.used,
        "model_429_seen": limited,
        "shared_spike_injected": meter.spiked,
        "setup_blocked": setup_blocked,
        "entry_forwarded": len(entries),
        "reduce_exit_forwarded": len(exits),
        "position_nonzero_at_stop": any(fake.positions.values()),
        "risk_state": after.risk_state.value,
        "paused": after.paused,
        "tp_result": report.get("tp_trigger_check", "NOT_STARTED"),
        "flat_confirmation": report.get("flat_confirmation", "NOT_CHECKED"),
        "phase_assumed_weights": dict(phases),
        "single_synthetic_minute_bucket": True,
        "observation_accelerated_to_seconds": 0.8,
        "actual_venue_used": False,
    }
    first_limited = next((i for i, row in enumerate(meter.rows) if row["status"] == 429), None)
    result["wire_attempts_after_first_model_429"] = (
        0 if first_limited is None else len(meter.rows) - first_limited - 1
    )
    output = os.getenv("SHADOW_REPORT_DIR")
    if output:
        dest = Path(output)
        dest.mkdir(exist_ok=True, parents=True)
        name = f"{mode}-seed{seed}-cap{cap}-unknown{unknown_cost}-dual{dual_cost}.json"
        (dest / name).write_text(
            json.dumps({"result": result, "request_trace": meter.rows}, indent=2) + "\n"
        )


def test_synthetic_history_matches_observed_operational_read_shape(opposite_case):  # noqa: F811
    store, c, fake, options = opposite_case
    seed_history(store, c, fake)
    sender = c.client.send
    meter = Meter(
        sender, fake, seed=0, unknown_cost=1, cap=10**9, spike=False, partial=False, options=options
    )
    c.client.send = meter
    c.client.settings = c.client.settings.model_copy(update={"demo_trading_enabled": False})
    c.client.sync_clock()
    initial = len(meter.rows)
    snap = operational_snapshot(c.client, store.read("DEMO-test"), store.now())
    rows = meter.rows[initial:]
    assert len(rows) == 14 and not any(snap.positions.values())
    assert sum(row["assumed_weight"] for row in rows) == 196
    assert Counter(row["path"] for row in rows)["/fapi/v1/order"] == 3
    assert Counter(row["path"] for row in rows)["/fapi/v1/algoOrder"] == 3


@pytest.mark.parametrize("dual_cost", [30, 35])
@pytest.mark.parametrize("unknown_cost", [1, 40])
def test_protected_300_second_idle_model_with_real_local_pongs_and_minute_resets(
    opposite_case,  # noqa: F811
    monkeypatch,
    unknown_cost,
    dual_cost,
):
    from services.execution import demo_stream

    store, old, fake, options = opposite_case
    seed_history(store, old, fake)
    options["trigger"] = False
    original = old.client.send
    meter = Meter(
        original,
        fake,
        seed=0,
        unknown_cost=unknown_cost,
        cap=6000,
        spike=False,
        partial=False,
        options=options,
        dual_cost=dual_cost,
        windowed=True,
    )

    class Pool:
        def __call__(self, *args):
            return meter(*args)

        def close(self):
            pass

        def diagnostics(self):
            return {}

    route = TPReadRouting(meter, pool=Pool())
    old.client.send = route
    c = PooledReadTPTriggerCoordinator(store, old.lease, old.client)
    c.client.settings = c.client.settings.model_copy(update={"demo_trading_enabled": False})
    c.client.sync_clock()
    c.refresh_metadata()
    c.reconcile()
    c.client.settings = c.client.settings.model_copy(update={"demo_trading_enabled": True})
    before = store.read("DEMO-test")

    async def run_idle():
        stop = asyncio.Event()
        virtual_elapsed = [0.0]
        base = demo_stream.EventInbox

        class IdleInbox(base):
            async def take(self, *, timeout=5):
                fake.at += timedelta(seconds=timeout)
                virtual_elapsed[0] += timeout
                if virtual_elapsed[0] >= 300:
                    stop.set()
                    await asyncio.Future()
                raise TimeoutError

        monkeypatch.setattr(demo_stream, "EventInbox", IdleInbox)

        async def handler(socket):
            await socket.wait_closed()

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]

            @asynccontextmanager
            async def connector(url):
                async with connect(f"ws://127.0.0.1:{port}") as socket:
                    yield socket

            async def local_session(coordinator, *, on_ready):
                worker = asyncio.create_task(
                    session(coordinator, connector=connector, on_ready=on_ready)
                )
                waiter = asyncio.create_task(stop.wait())
                try:
                    done, _ = await asyncio.wait(
                        {worker, waiter}, return_when=asyncio.FIRST_COMPLETED
                    )
                    if waiter in done and stop.is_set():
                        worker.cancel()
                        try:
                            await worker
                        except asyncio.CancelledError:
                            pass
                    else:
                        await worker
                finally:
                    waiter.cancel()
                    if not worker.done():
                        worker.cancel()

            monkeypatch.setattr(validation, "session", local_session)
            monkeypatch.setattr(tp_module, "OBSERVATION_SECONDS", 30)
            report = await tp_module.run(c, uuid4(), entry_style="OPPOSITE_QUOTE")
        return report, virtual_elapsed[0]

    try:
        report, elapsed = asyncio.run(run_idle())
    finally:
        route.close()
        c.client.send = original
    after = store.read("DEMO-test")
    assert elapsed == 300 and not any(r["status"] == 429 for r in meter.rows)
    assert max(meter.window_peaks.values()) <= 6000
    assert report["flat_confirmation"] == "VERIFIED_FLAT" and not any(fake.positions.values())
    assert report["tp_trigger_check"] == "NOT_ACCEPTED" and len(posts(fake)) == 2
    assert report["history_and_baselines_preserved"]
    assert all(after.orders[k] == value for k, value in before.orders.items())
    result = {
        "mode": "virtual_300s_no_tp",
        "dual_side_assumed_delta": dual_cost,
        "unmeasured_route_assumption": unknown_cost,
        "virtual_seconds": elapsed,
        "real_local_websocket_handshake_and_pongs": True,
        "synthetic_event_timeout_driver": True,
        "modeled_total_request_weight": sum(r["assumed_weight"] for r in meter.rows),
        "http_attempts": len(meter.rows),
        "per_minute_peak": [
            {"minute_offset": k - meter.window_origin, "peak": v}
            for k, v in sorted(meter.window_peaks.items())
        ],
        "flat_confirmation": report["flat_confirmation"],
        "entry_count": report["new_entry_post_count"],
        "reduce_exit_count": report["operator_exit_post_attempts"],
        "actual_venue_used": False,
        "published_limit_model": 6000,
        "observed_endpoint_weights_not_proven": True,
    }
    output = os.getenv("SHADOW_REPORT_DIR")
    if output:
        dest = Path(output)
        dest.mkdir(exist_ok=True, parents=True)
        (dest / f"virtual300-unknown{unknown_cost}-dual{dual_cost}.json").write_text(
            json.dumps({"result": result, "request_trace": meter.rows}, indent=2) + "\n"
        )
