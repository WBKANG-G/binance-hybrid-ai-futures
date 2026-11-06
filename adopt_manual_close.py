"""Scoped signed-proof adoption of one operator-closed DEMO position; never submits orders."""

import argparse
import hashlib
import json
import os
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4, uuid5

import yaml
from sqlalchemy import create_engine, select

from integrations.binance_usdm.demo import DemoClient, DemoSettings
from integrations.binance_usdm.demo_snapshot import (
    ORDER_KEYS,
    operational_snapshot,
    pick,
    signed_number,
)
from integrations.binance_usdm.recovery_transport import recovery_connection
from packages.domain.config import UniqueKeyLoader, database_url
from packages.domain.contracts import PositionState
from packages.persistence.demo import DemoStore, accounts
from scripts.demo_key_session import credentials
from services.execution.demo_models import DemoLedger
from services.execution.demo_recovery import _check_loss_gates, local_period_status
from services.execution.demo_recovery_budget import RecoveryBudget, RecoveryReadClient
from services.risk.paper import AccountValue

ACCOUNT = "DEMO-WINDOWS-ISOLATED-BTC-V1"
INCIDENT = "041d1f35-870e-46cb-aad2-ed6059bc1095"
RECEIPT = str(uuid5(UUID(INCIDENT), "signed-operator-manual-close-v1"))
ENTRY = "28668115919"
CLOSE = "28668119491"
QUANTITY = Decimal("0.0007")
EXPECTED_TRADES = {
    "554837511": (ENTRY, "BUY", "75725.10", "0", "0.02120302", 1793947690123),
    "554838531": (CLOSE, "SELL", "75685.20", "-0.02793000", "0.02119185", 1793947973987),
}
SOURCES = {
    "integrations/binance_usdm/demo.py": "c5ec1f89d645dcd124a71cdf9cb39884cc9cfa5a2493636baf1c08db2c581a81",
    "integrations/binance_usdm/demo_snapshot.py": "02e5d789280f3ed8aa2667319248982d5cd4eedd2fb8b72e70aef8785e57ed6a",
    "integrations/binance_usdm/recovery_transport.py": "11d66dd5e05b4707b5794707df0fa889be4cbb1204fcd7a0ec23d9c6008ea791",
    "packages/domain/config.py": "c2bd7a039e3ea007a1e34a6dbdd058cc626e2690cc8d55b0761cfd2085bf311d",
    "packages/domain/contracts.py": "71c0a95faad586d6e3f342d83b6b95a4772cb6e2c1808b44fc51fdf9f1332947",
    "packages/persistence/demo.py": "40f9a33b8eeda28c96f36de35656e4667a2d2461ce9de79dc4b19a687cd5cd5a",
    "services/execution/models.py": "5d362e33788a304e6f2f63f22868454b3804b103277a6fe4a1297c7e50e10a5d",
    "services/execution/demo_models.py": "175def10d7378661e82616b02ec87d6dbee4ffdeb4eb94437ce682206168dfb0",
    "services/execution/demo_recovery.py": "ca663f098d2f7c462d1f225f4ade91d743169175f125c574794dc58175c8c76e",
    "services/execution/demo_recovery_budget.py": "2c7aa260fd708948b0ae7854257de03bc8b59c86da85180ea5230b07e8df772b",
    "services/execution/paper.py": "1a847e50fc78d48846fadc9953fad07aed5bbdf83f1e30986d39fef0e0d9fc13",
    "services/risk/paper.py": "7afe00460abae04dda795ab0dba8b8ad84877fa25686342c1fbce3f12ac80832",
    "scripts/demo_key_session.py": "85c698bc8bbad98bad4051c346a5e4e0a3a3bd22f8bbb90a362f835cadc633dd",
    "configs/demo-isolated.example.yaml": "d93e85c2e056ffbbc3dac5fab9f0e5bf28032fd16f41bbff1f3ae81fb00882ca",
    "scripts/check_demo_tp_trigger.py": "0bef5c186d6e4a5c226f66736085585cf1d209d13ef1bd8622666d44d52966f9",
    "integrations/binance_usdm/demo_read_routing.py": "a6e96b21135882a272cb5c97f86ddc14c3a5f4fa79dd115e40397252c679a66d",
}


class Blocked(ValueError):
    pass


class TradeMismatch(Blocked):
    def __init__(self, fields):
        super().__init__("SIGNED_TRADE_PROOF_MISMATCH")
        self.fields = sorted(fields)


def require(ok, code):
    if not ok:
        raise Blocked(code)


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def read_local(store):
    with store.engine.connect() as conn:
        conn.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        conn.exec_driver_sql("SET LOCAL statement_timeout='3s'")
        row = (
            conn.execute(select(accounts).where(accounts.c.account_id == ACCOUNT)).mappings().one()
        )
    return row["owner"], row["fence"], row["lease_until"], DemoLedger.model_validate(row["state"])


def context(ledger, client):
    require(
        not client.settings.demo_trading_enabled
        and client.settings.margin_mode == ledger.margin_mode == "ISOLATED"
        and client.settings.symbols == ledger.symbols == ("BTCUSDT",)
        and client.settings.policy == ledger.policy,
        "READ_ONLY_CONTEXT_REQUIRED",
    )
    require(
        ledger.paused
        and ledger.risk_state.value == "NORMAL"
        and ledger.income_since_ms is not None,
        "NORMAL_PAUSED_INITIALIZED_REQUIRED",
    )
    order = ledger.orders.get(INCIDENT)
    require(order is not None, "INCIDENT_MISSING")
    require(
        order.signal.symbol == "BTCUSDT"
        and order.signal.direction == "LONG"
        and order.signal.purpose == "DEMO_VALIDATION"
        and order.quantity == order.filled == order.open_quantity == QUANTITY
        and order.state.value == "FILLED"
        and order.position_state.value == "OPEN_UNPROTECTED"
        and not order.remaining
        and not order.protected
        and order.exit_id is None
        and order.exit_state == "NONE"
        and str(order.server_order_id) == ENTRY
        and order.average_price == Decimal("75725.10")
        and order.algo_id
        and order.tp_algo_id,
        "INCIDENT_SCOPE_MISMATCH",
    )
    require(
        ledger.validation_entry_styles.get(INCIDENT) == "OPPOSITE_QUOTE"
        and ledger.operations.get(str(uuid5(UUID(INCIDENT), "tp-trigger-observation")))
        == "TP_TRIGGER:NOT_ACCEPTED",
        "INCIDENT_RESULT_MISMATCH",
    )
    require(
        all(
            k == INCIDENT
            or (
                not o.open_quantity
                and not o.remaining
                and not o.actual_risk
                and not o.protected
                and o.position_state.value == "CLOSED"
            )
            for k, o in ledger.orders.items()
        )
        and all(not q or (s == "BTCUSDT" and q == QUANTITY) for s, q in ledger.positions.items()),
        "OTHER_LOCAL_EXPOSURE",
    )
    return order


class Budget(RecoveryBudget):
    def check(self):
        super().check()
        require(self.clock() - self.started < 20, "MANUAL_REVIEW_TIME_BUDGET")

    def signed(self):
        require(self.signed_queries < 64, "MANUAL_REVIEW_QUERY_BUDGET")
        super().signed()

    def probe(self):
        require(self.clock_probes < 3, "MANUAL_REVIEW_CLOCK_BUDGET")
        super().probe()


def prove(store, client, ledger):
    order = context(ledger, client)
    require(
        local_period_status(store, ledger)["period_keys_match"] is True, "PERIOD_BASELINE_MISMATCH"
    )
    with recovery_connection(client):
        reader = RecoveryReadClient(client, Budget())
        reader.sync_clock()
        entry = reader.request("GET", "/fapi/v1/order", {"symbol": "BTCUSDT", "orderId": ENTRY})
        closing = reader.request("GET", "/fapi/v1/order", {"symbol": "BTCUSDT", "orderId": CLOSE})
        for raw, identity, side, kind, reduce, price in (
            (entry, ENTRY, "BUY", "LIMIT", False, "75725.10"),
            (closing, CLOSE, "SELL", "MARKET", True, "75685.20"),
        ):
            require(
                isinstance(raw, dict)
                and str(raw.get("orderId")) == identity
                and raw.get("symbol") == "BTCUSDT"
                and raw.get("side") == side
                and raw.get("positionSide") == "BOTH"
                and raw.get("status") == "FILLED"
                and raw.get("type") == kind
                and raw.get("reduceOnly") is reduce
                and signed_number(raw.get("origQty"))
                == signed_number(raw.get("executedQty"))
                == QUANTITY
                and signed_number(raw.get("avgPrice")) == Decimal(price),
                "SIGNED_ORDER_PROOF_MISMATCH",
            )
        require(
            entry.get("clientOrderId") == order.client_id
            and isinstance(closing.get("clientOrderId"), str)
            and closing["clientOrderId"].startswith("web_")
            and closing["clientOrderId"] not in ledger.requests,
            "SIGNED_ORDER_IDENTITY_MISMATCH",
        )
        start = int(order.signal.signal_closed_candle_at.timestamp() * 1000)
        end = client.clock_ms() + client.offset
        require(0 < end - start <= 604800000, "TRADE_WINDOW_OUT_OF_SCOPE")
        trades = reader.request(
            "GET",
            "/fapi/v1/userTrades",
            {"symbol": "BTCUSDT", "startTime": str(start), "endTime": str(end), "limit": "1000"},
        )
        require(
            isinstance(trades, list) and len(trades) == len(EXPECTED_TRADES), "TRADE_COHORT_CHANGED"
        )
        require(
            all(isinstance(t, dict) for t in trades)
            and len({str(t.get("id")) for t in trades}) == len(trades)
            and {str(t.get("id")) for t in trades} == set(EXPECTED_TRADES),
            "TRADE_IDENTITY_MISMATCH",
        )
        for trade in trades:
            identity, side, price, pnl, fee, stamp = EXPECTED_TRADES[str(trade["id"])]
            checks = {
                "symbol": trade.get("symbol") == "BTCUSDT",
                "orderId": str(trade.get("orderId")) == identity,
                "side": trade.get("side") == side,
                "positionSide": trade.get("positionSide") == "BOTH",
                "qty": signed_number(trade.get("qty")) == QUANTITY,
                "price": signed_number(trade.get("price")) == Decimal(price),
                "realizedPnl": signed_number(trade.get("realizedPnl")) == Decimal(pnl),
                "commission": signed_number(trade.get("commission")) == Decimal(fee),
                "commissionAsset": trade.get("commissionAsset") == "USDT",
                "time": type(trade.get("time")) is int
                and trade["time"] == stamp
                and start <= stamp <= end,
            }
            mismatched = [key for key, value in checks.items() if not value]
            if mismatched:
                raise TradeMismatch(mismatched)
        require(
            int(closing["time"]) == int(closing["updateTime"]) == EXPECTED_TRADES["554838531"][-1]
            and int(entry["time"]) <= EXPECTED_TRADES["554837511"][-1] <= int(entry["updateTime"]),
            "ORDER_TRADE_TIME_MISMATCH",
        )
        require(
            signed_number(entry.get("price")) == order.signal.suggested_limit_price,
            "ENTRY_LIMIT_PRICE_MISMATCH",
        )
        first = operational_snapshot(reader, ledger, store.now())
        second = operational_snapshot(reader, ledger, store.now())
        require(
            first.fingerprint == second.fingerprint and first.transfers == second.transfers,
            "SNAPSHOT_CHANGED",
        )
        require(
            not any(second.positions.values())
            and not second.margin_used
            and not any(r["status"] in {"NEW", "PARTIALLY_FILLED"} for r in second.orders.values())
            and not any(r["algoStatus"] == "NEW" for r in second.algos.values()),
            "REMOTE_NOT_EMPTY",
        )
        require(
            second.transfers == ledger.income_seen
            and second.leverage.get("BTCUSDT") == 2
            and second.margin_types.get("BTCUSDT") == "ISOLATED",
            "TRANSFER_OR_SETTINGS_CHANGED",
        )
        for identifier, kind, price in (
            (order.algo_id, "STOP_MARKET", order.signal.initial_stop_price),
            (order.tp_algo_id, "TAKE_PROFIT_MARKET", order.signal.target_price),
        ):
            raw = second.algos.get(identifier)
            require(
                raw
                and raw["clientAlgoId"] == identifier
                and raw["symbol"] == "BTCUSDT"
                and raw["side"] == "SELL"
                and raw["positionSide"] == "BOTH"
                and raw["orderType"] == kind
                and raw["algoType"] == "CONDITIONAL"
                and raw["closePosition"] == "true"
                and raw["workingType"] == "MARK_PRICE"
                and raw["priceProtect"] == "false"
                and raw["algoStatus"] in {"CANCELED", "EXPIRED"}
                and signed_number(raw["triggerPrice"]) == price,
                "ORIGINAL_PROTECTION_NOT_TERMINAL",
            )
        require(
            second.orders.get(order.client_id) == pick(entry, ORDER_KEYS),
            "ENTRY_CHANGED_DURING_PROOF",
        )
        require(
            second.equity - ledger.net_transfers <= ledger.high_water,
            "HIGH_WATER_OBSERVATION_REQUIRES_SEPARATE_REVIEW",
        )
        _check_loss_gates(store, ledger, second.equity)
        require(
            local_period_status(store, ledger)["period_keys_match"] is True,
            "PERIOD_BASELINE_MISMATCH",
        )
        reader.budget.check()
        proof = {
            "entry_order_id": ENTRY,
            "manual_close_order_id": CLOSE,
            "manual_close_client_id": closing["clientOrderId"],
            "trade_ids": sorted(EXPECTED_TRADES),
            "source": "SIGNED_OPERATOR_MANUAL_CLOSE",
            "exchange_fingerprint": second.fingerprint,
            "net_usdt": str(
                sum(
                    (
                        signed_number(t["realizedPnl"]) - signed_number(t["commission"])
                        for t in trades
                    ),
                    Decimal(0),
                )
            ),
            "signed_queries": reader.budget.signed_queries,
            "original_order": order.model_dump(mode="json"),
        }
        return second, proof


def adopt(store, client, *, apply=False, progress=None):
    progress = progress if progress is not None else {}
    progress.update(application_may_have_completed=False, lease_changes_possible=False)
    initial = read_local(store)
    owner, _, _, before = initial
    require(not client.settings.demo_trading_enabled, "READ_ONLY_CONTEXT_REQUIRED")
    require(owner is None, "ACTIVE_OWNER_BLOCKED")
    if RECEIPT in before.operations:
        receipt = json.loads(before.operations[RECEIPT])
        require(
            receipt.get("incident") == INCIDENT
            and receipt.get("entry_order_id") == ENTRY
            and receipt.get("manual_close_order_id") == CLOSE
            and before.orders[INCIDENT].position_state.value == "CLOSED"
            and not before.orders[INCIDENT].open_quantity
            and not before.orders[INCIDENT].actual_risk
            and not before.orders[INCIDENT].remaining
            and not before.orders[INCIDENT].protected
            and before.paused,
            "RECEIPT_CONFLICT",
        )
        return {
            "manual_close_adoption": "ALREADY_RECORDED_NO_ACTION",
            "database_changed": False,
            "current_exchange_rechecked": False,
            "order_writes": 0,
        }
    context(before, client)
    if not apply:
        snap, proof = prove(store, client, before)
        require(read_local(store) == initial, "LOCAL_STATE_CHANGED")
        return {
            "manual_close_adoption": "REVIEW_ONLY_KEEP_PAUSED",
            "database_changed": False,
            "manual_close_proven": True,
            "net_usdt": proof["net_usdt"],
            "order_writes": 0,
        }
    progress["lease_changes_possible"] = True
    lease = store.acquire(ACCOUNT, uuid4())
    committed = False
    try:
        current = store.read(ACCOUNT)
        expected = before.model_copy(deep=True, update={"reconciled": False, "stream_ready": False})
        require(current == expected, "LOCAL_STATE_CHANGED")
        snap, proof = prove(store, client, current)

        def write(ledger):
            require(ledger == current, "LOCAL_STATE_CHANGED")
            require(
                0
                <= (store.now() - snap.as_of).total_seconds()
                <= ledger.policy.max_snapshot_age_seconds,
                "PROOF_STALE",
            )
            require(
                local_period_status(store, ledger)["period_keys_match"] is True,
                "PERIOD_BASELINE_MISMATCH",
            )
            _check_loss_gates(store, ledger, snap.equity)
            original = ledger.model_dump(mode="json")
            order = ledger.orders[INCIDENT]
            order.open_quantity = order.actual_risk = Decimal(0)
            order.protected = False
            order.position_state = PositionState.CLOSED
            ledger.positions = snap.positions.copy()
            ledger.value = AccountValue(
                as_of=snap.as_of,
                initial=ledger.value.initial,
                realized=snap.equity - ledger.value.initial - ledger.net_transfers,
                net_deposits=ledger.net_transfers,
            )
            ledger.available_balance, ledger.margin_used = snap.available_balance, snap.margin_used
            ledger.isolated_wallets, ledger.isolated_margins = (
                snap.isolated_wallets.copy(),
                snap.isolated_margins.copy(),
            )
            ledger.operations[RECEIPT] = json.dumps(
                {
                    "incident": INCIDENT,
                    **proof,
                    "acknowledged_at_utc": store.now().isoformat(),
                    "prior_ledger_sha256": digest(original),
                },
                sort_keys=True,
                default=str,
            )
            # External closure is recorded separately. Do not invent a DX request,
            # application exit, TP success or cleared failure diagnostic.
            allowed = {
                "orders",
                "operations",
                "positions",
                "value",
                "available_balance",
                "margin_used",
                "isolated_wallets",
                "isolated_margins",
            }
            after = ledger.model_dump(mode="json")
            require(
                all(original[k] == after[k] for k in original.keys() - allowed),
                "UNRELATED_LEDGER_CHANGE",
            )
            require(
                all(
                    original["orders"][k] == after["orders"][k]
                    for k in ledger.orders
                    if k != INCIDENT
                ),
                "HISTORY_CHANGED",
            )
            permitted_order = {"open_quantity", "actual_risk", "protected", "position_state"}
            require(
                all(
                    original["orders"][INCIDENT][k] == after["orders"][INCIDENT][k]
                    for k in original["orders"][INCIDENT].keys() - permitted_order
                ),
                "ENTRY_OR_EXIT_HISTORY_CHANGED",
            )

        progress["application_may_have_completed"] = True
        store.change(lease, "DEMO_SIGNED_OPERATOR_MANUAL_CLOSE_ADOPTED", write)
        committed = True
    finally:
        try:
            store.release(lease)
        except Exception:
            raise Blocked(
                "RELEASE_UNVERIFIED_APPLICATION_MAY_HAVE_COMPLETED"
                if committed
                else "RELEASE_UNVERIFIED"
            ) from None
    final = read_local(store)
    require(
        final[0] is None
        and final[1] == lease.fence
        and final[-1].orders[INCIDENT].position_state.value == "CLOSED"
        and not final[-1].orders[INCIDENT].open_quantity
        and final[-1].paused
        and final[-1].risk_state == before.risk_state,
        "POSTCHECK_FAILED_APPLICATION_MAY_HAVE_COMPLETED",
    )
    return {
        "manual_close_adoption": "SIGNED_EXTERNAL_CLOSE_ADOPTED_ENTRIES_STILL_PAUSED",
        "incident": INCIDENT,
        "manual_close_order_id": CLOSE,
        "position_state": "CLOSED",
        "open_quantity": "0",
        "risk_state": final[-1].risk_state.value,
        "paused": True,
        "leader_released": True,
        "entry_and_failed_trial_preserved": True,
        "application_exit_history_preserved": True,
        "risk_baselines_and_high_water_preserved": True,
        "unresolved_requests_preserved": True,
        "financial_value_refreshed_from_signed_snapshot": True,
        "signed_queries": proof["signed_queries"],
        "database_changed": True,
        "order_writes": 0,
        "orders_enabled": False,
        "live_trading_enabled": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    engine = None
    progress = {"application_may_have_completed": False, "lease_changes_possible": False}
    try:
        require(
            os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "false"
            and os.getenv("TRADING_MODE", "PAPER").upper() != "LIVE",
            "LIVE_CONTEXT_FORBIDDEN",
        )
        require(
            SOURCES
            and all(
                hashlib.sha256(Path(name).read_bytes()).hexdigest() == sha
                for name, sha in SOURCES.items()
            ),
            "SOURCE_HASH_MISMATCH",
        )
        cfg = DemoSettings.model_validate(
            yaml.load(
                Path("configs/demo-isolated.example.yaml").read_text(), Loader=UniqueKeyLoader
            )
        ).model_copy(update={"demo_trading_enabled": False})
        client = DemoClient(cfg, credentials(prompt=False))
        engine = create_engine(
            database_url().get_secret_value(), connect_args={"connect_timeout": 2}
        )
        report = adopt(DemoStore(engine), client, apply=args.apply, progress=progress)
        code = 0
    except Exception as error:
        # Only fixed local codes are printable; private SDK/DB responses stay hidden.
        failure = str(error) if isinstance(error, Blocked) else "REVIEW_OR_DATABASE_UNAVAILABLE"
        report = {
            "manual_close_adoption": "BLOCKED_KEEP_PAUSED_NO_AUTOMATIC_RETRY",
            "failure": failure,
            **progress,
            "mismatched_trade_fields": error.fields if isinstance(error, TradeMismatch) else [],
            "order_writes": 0,
            "orders_enabled": False,
            "live_trading_enabled": False,
        }
        code = 2
    finally:
        if engine is not None:
            engine.dispose()
    print(json.dumps(report), flush=True)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
