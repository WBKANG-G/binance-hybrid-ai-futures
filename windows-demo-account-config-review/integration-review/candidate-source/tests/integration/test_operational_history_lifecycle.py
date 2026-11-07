"""Real isolated PG/local WSS with only synthetic fills; no runtime entry point."""

# ruff: noqa: F811
import json
from pathlib import Path

import pytest

from integrations.binance_usdm import demo_snapshot as canonical_snapshot
from tests.fixtures import approved_snapshot_v1
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_opposite_tp import (
    exercise_opposite,
    opposite_case,  # noqa: F401
)
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_tp_trigger_check import case  # noqa: F401
from tests.unit.test_readiness_closed_history import INCIDENT, RECEIPT, basis  # noqa: F401


@pytest.mark.parametrize(
    "mode",
    [
        "tp",
        "no_tp",
        "unfilled",
        "partial",
        "bad_qty",
        "missing_cancel",
        "mixed_readers",
        "rate_limit",
    ],
)
def test_active_lifecycle_preserves_all_original_proofs_and_old_history(
    opposite_case, basis, monkeypatch, mode
):
    from decimal import Decimal

    use_candidate = True

    store, c, fake, options = opposite_case
    _, archived = basis
    old = archived.orders[INCIDENT].model_copy(deep=True)
    receipt = json.loads(archived.operations[RECEIPT])
    receipt["acknowledged_at_utc"] = store.now().isoformat()

    def seed(s):
        s.orders[INCIDENT] = old
        s.operations.update(archived.operations)
        s.operations[RECEIPT] = json.dumps(receipt)

    store.change(c.lease, "SYNTHETIC_ADOPTED_MANUAL_CLOSE_HISTORY", seed)
    fake.orders[old.client_id] = {
        "symbol": "BTCUSDT",
        "clientOrderId": old.client_id,
        "orderId": "28668115919",
        "side": "BUY",
        "type": "LIMIT",
        "status": "FILLED",
        "origQty": "0.0007",
        "executedQty": "0.0007",
        "avgPrice": "0",
        "price": "100",
        "positionSide": "BOTH",
        "reduceOnly": False,
    }
    for identity, kind, trigger in [
        (old.algo_id, "STOP_MARKET", "90"),
        (old.tp_algo_id, "TAKE_PROFIT_MARKET", "120"),
    ]:
        fake.algos[identity] = {
            "symbol": "BTCUSDT",
            "clientAlgoId": identity,
            "algoId": identity + "1",
            "side": "SELL",
            "orderType": kind,
            "algoStatus": "EXPIRED",
            "triggerPrice": trigger,
            "closePosition": True,
            "positionSide": "BOTH",
            "workingType": "MARK_PRICE",
            "priceProtect": False,
            "algoType": "CONDITIONAL",
            "actualOrderId": "",
        }
    if mode in {"no_tp", "partial", "unfilled", "rate_limit"}:
        options["trigger"] = False
    if mode in {"partial", "unfilled"}:
        options["fill"] = False
    if mode == "bad_qty":
        options["tamper"] = True
    if mode == "missing_cancel":
        options["cancel_query"] = True
    original_send = c.client.send
    rate_mark = []

    def sender(method, path, query, key):
        from urllib.parse import parse_qs

        params = {k: v[0] for k, v in parse_qs(query.get_secret_value()).items()}
        if mode == "rate_limit" and rate_mark:
            from integrations.binance_usdm.rest import RESTResponse

            fake.calls.append((method, path, params))
            return RESTResponse(429, b"synthetic-non-json", {"Retry-After": "120"})
        response = original_send(method, path, query, key)
        if (
            mode == "partial"
            and method == "POST"
            and path == "/fapi/v1/order"
            and params.get("type") == "LIMIT"
        ):
            fake.fill(params["newClientOrderId"], Decimal("0.0003"), Decimal("80000"))
        if mode == "rate_limit" and not rate_mark and path == "/fapi/v3/account":
            state = store.read("DEMO-test")
            if any(
                k != INCIDENT and e.get("stop_verified") and e.get("target_verified")
                for k, e in state.validation_evidence.items()
            ):
                rate_mark.append(len(fake.calls))
        return response

    c.client.send = sender
    per_snapshot = []
    historical = []
    original_snapshot = canonical_snapshot._snapshot

    def observed(client, ledger, now, *, include_closed_history=True):
        start = len(fake.calls)
        result = original_snapshot(
            client, ledger, now, include_closed_history=include_closed_history
        )
        calls = fake.calls[start:]
        old_reads = sum(
            p.get("origClientOrderId") == old.client_id
            or p.get("clientAlgoId") in {old.algo_id, old.tp_algo_id}
            for _, _, p in calls
        )
        if include_closed_history:
            assert old_reads == 3
            historical.append(True)
        else:
            assert old_reads == 0
            per_snapshot.append({"requests": len(calls), "archived_id_reads": old_reads})
        return result

    # Observation hook only; no operational consumer or returned response is replaced.
    monkeypatch.setattr(canonical_snapshot, "_snapshot", observed)
    if mode == "mixed_readers":
        from services.execution import demo_preview

        monkeypatch.setattr(demo_preview, "snapshot", approved_snapshot_v1.operational_snapshot)
    c.reconcile()
    before = store.read("DEMO-test")
    start = len(fake.calls)
    report = exercise_opposite(opposite_case, monkeypatch)
    after = store.read("DEMO-test")
    assert after.orders[INCIDENT] == before.orders[INCIDENT]
    assert after.operations[RECEIPT] == before.operations[RECEIPT]
    assert (
        after.daily_base == before.daily_base
        and after.weekly_base == before.weekly_base
        and after.high_water == before.high_water
    )
    assert report["history_and_baselines_preserved"] and after.paused
    if mode == "mixed_readers" and use_candidate:
        assert report["entry_post_attempts"] == report["new_entry_post_count"] == 0
        assert report["tp_trigger_check"] == "NOT_ACCEPTED"
        assert len(historical) == 5 and not any(fake.positions.values())
        folder = Path("synthetic_reports/operational-history-integration")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "mixed_readers-candidateTrue.json").write_text(
            json.dumps(
                {
                    "synthetic_only": True,
                    "isolated_checkout_parser_applied": True,
                    "mixed_reader_fingerprint_gate_blocked": True,
                    "new_entry_posts": 0,
                    "full_historical_snapshot_count": len(historical),
                    "result": report,
                },
                indent=2,
            )
            + "\n"
        )
        return
    assert report["entry_post_attempts"] == report["new_entry_post_count"] == 1
    if mode == "rate_limit":
        assert report["tp_trigger_check"] == "NOT_ACCEPTED"
        assert report["flat_confirmation"] == "UNKNOWN" and report["risk_state"] == "EMERGENCY"
        assert any(fake.positions.values()) and c.client.cooldown_until > c.client.clock_ms()
        assert rate_mark and len(fake.calls) == rate_mark[0] + 1
        assert all(m == "GET" for m, _, _ in fake.calls[rate_mark[0] :])
        folder = Path("synthetic_reports/operational-history-integration")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / ("rate_limit-candidate" + str(use_candidate) + ".json")).write_text(
            json.dumps(
                {
                    "synthetic_only": True,
                    "isolated_checkout_parser_applied": True,
                    "windows_runtime_installed": False,
                    "cooldown_preserved": True,
                    "no_retry_after_first429": True,
                    "result": report,
                },
                indent=2,
            )
            + "\n"
        )
        return
    assert report["operator_exit_post_attempts"] <= 1
    assert len(historical) == 5 and not any(fake.positions.values())
    assert per_snapshot and all(
        r["archived_id_reads"] == (0 if use_candidate else 3) for r in per_snapshot
    )
    if mode in {"tp", "mixed_readers"}:
        assert report["tp_trigger_check"] == "TP_FILLED_THEN_REMAINING_SL_CANCELED_FLAT_VERIFIED"
        assert (
            report["signed_tp_fill_before_sl_delete_verified"]
            and report["sl_cancel_query_verified"]
        )
        assert report["operator_exit_post_attempts"] == 0 and report["sl_delete_attempts"] == 1
    else:
        assert report["tp_trigger_check"] == "NOT_ACCEPTED"
    summary = {
        "synthetic_only": True,
        "isolated_checkout_parser_applied": True,
        "mode": mode,
        "candidate": use_candidate,
        "operational_snapshot_count": len(per_snapshot),
        "operational_snapshot_rows": per_snapshot,
        "full_historical_snapshot_count": len(historical),
        "full_historical_archived_id_reads": 15,
        "validation_requests": len(fake.calls) - start,
        "hypothetical_old_id_weight1_saving": 3 * len(per_snapshot) if use_candidate else 0,
        "actual_weight_saved_verified": False,
        "windows_runtime_installed": False,
        "result": report,
    }
    folder = Path("synthetic_reports/operational-history-integration")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / (mode + "-candidate" + str(use_candidate) + ".json")).write_text(
        json.dumps(summary, indent=2) + "\n"
    )
