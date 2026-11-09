"""Read-only API install guard after the signed manual-close adoption."""

import argparse
import hashlib
import json
import os
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import UUID, uuid5

import yaml
from sqlalchemy import create_engine, select

from integrations.binance_usdm.demo import DemoClient, DemoSettings
from integrations.binance_usdm.demo_snapshot import operational_snapshot
from integrations.binance_usdm.recovery_transport import recovery_connection
from packages.domain.config import UniqueKeyLoader, database_url
from packages.persistence.demo import DemoStore, accounts
from scripts.demo_key_session import credentials
from services.execution.demo_empty_db_restart import empty
from services.execution.demo_models import DemoLedger
from services.execution.demo_recovery import _check_loss_gates, local_period_status
from services.execution.demo_recovery_budget import RecoveryReadClient

ACCOUNT = "DEMO-WINDOWS-ISOLATED-BTC-V1"
INCIDENT = "041d1f35-870e-46cb-aad2-ed6059bc1095"
RECEIPT = str(uuid5(UUID(INCIDENT), "signed-operator-manual-close-v1"))
OLD_PINS = {
    "configs/demo-isolated.example.yaml": "d93e85c2e056ffbbc3dac5fab9f0e5bf28032fd16f41bbff1f3ae81fb00882ca",
    "integrations/binance_usdm/demo.py": "c5ec1f89d645dcd124a71cdf9cb39884cc9cfa5a2493636baf1c08db2c581a81",
    "integrations/binance_usdm/demo_rate_limits.py": None,
    "integrations/binance_usdm/demo_read_routing.py": "a6e96b21135882a272cb5c97f86ddc14c3a5f4fa79dd115e40397252c679a66d",
    "integrations/binance_usdm/demo_snapshot.py": "02e5d789280f3ed8aa2667319248982d5cd4eedd2fb8b72e70aef8785e57ed6a",
    "integrations/binance_usdm/filters.py": "0279613f1a17a2bc7ffd50e30be2b6fd1aed8ba240219e8e893b754a067d95ec",
    "integrations/binance_usdm/orders.py": "2ab366265f7d35356ee9636c6aad4b594b518beeb636407515a57386b3e8441f",
    "integrations/binance_usdm/recovery_transport.py": "11d66dd5e05b4707b5794707df0fa889be4cbb1204fcd7a0ec23d9c6008ea791",
    "integrations/binance_usdm/rest.py": "9a14edc40fa6365a50fe57c9ddeff4e866044e00287e7c5b680d8bb3dbd0ad52",
    "packages/domain/config.py": "c2bd7a039e3ea007a1e34a6dbdd058cc626e2690cc8d55b0761cfd2085bf311d",
    "packages/domain/contracts.py": "71c0a95faad586d6e3f342d83b6b95a4772cb6e2c1808b44fc51fdf9f1332947",
    "packages/persistence/demo.py": "40f9a33b8eeda28c96f36de35656e4667a2d2461ce9de79dc4b19a687cd5cd5a",
    "scripts/check_demo_tp_trigger.py": "0bef5c186d6e4a5c226f66736085585cf1d209d13ef1bd8622666d44d52966f9",
    "scripts/demo_key_session.py": "85c698bc8bbad98bad4051c346a5e4e0a3a3bd22f8bbb90a362f835cadc633dd",
    "services/execution/demo.py": "deb90dd8ddd9df627054a11c76f8dd8b09e364f2aaaca8f82b7fd0c237c444ab",
    "services/execution/demo_empty_db_restart.py": "6adcc8284b43050bc4a115f2796c001d424ba7e0261654c099075fb3d950e39f",
    "services/execution/demo_event_clock.py": "924f3b88c9092355bb4b4ada3114944848208ab5b44ab73ee5b45d5fc1a29bfb",
    "services/execution/demo_event_inbox.py": "6d9be8c64e144592a5c2343c1db6e4dfc1dee65aa65eeebc08bd52b5aa633281",
    "services/execution/demo_flat_transport.py": "93f878c9869ad397a52546f0aa976bbe8071366eff36c7d0be6b1329df757401",
    "services/execution/demo_models.py": "175def10d7378661e82616b02ec87d6dbee4ffdeb4eb94437ce682206168dfb0",
    "services/execution/demo_protected_worker_check.py": "70d9f0a6ec74223e8a7a82ec55c976288c10fb13c01c9b478f49f1a0fd408709",
    "services/execution/demo_recovery.py": "ca663f098d2f7c462d1f225f4ade91d743169175f125c574794dc58175c8c76e",
    "services/execution/demo_recovery_budget.py": "2c7aa260fd708948b0ae7854257de03bc8b59c86da85180ea5230b07e8df772b",
    "services/execution/demo_stream.py": "409098cb05abbfb97eb9b9f1db2070ca6bb5782bc6dd7542311e4eb703b6f456",
    "services/execution/demo_tp_trigger_check.py": "6b82ce5947480f87a58c194628853322496c90545a47713f4e07dd1fc0414dc8",
    "services/execution/demo_validation.py": "d703860caf01135b3f5e82a444adc5afde1795e1f4d48bbd515cc4b9de5666a0",
    "services/execution/models.py": "5d362e33788a304e6f2f63f22868454b3804b103277a6fe4a1297c7e50e10a5d",
    "services/execution/paper.py": "1a847e50fc78d48846fadc9953fad07aed5bbdf83f1e30986d39fef0e0d9fc13",
    "services/risk/paper.py": "7afe00460abae04dda795ab0dba8b8ad84877fa25686342c1fbce3f12ac80832",
}
NEW_PINS = {
    "configs/demo-isolated.example.yaml": "d93e85c2e056ffbbc3dac5fab9f0e5bf28032fd16f41bbff1f3ae81fb00882ca",
    "integrations/binance_usdm/demo.py": "b052fd6a68b0ffcb358567e1fbf96fc529005a21e2467490207c205c41debb58",
    "integrations/binance_usdm/demo_rate_limits.py": "d72af60f10a74baea689e11ef8f11b5003f2ab9f89e539290c45b93f2f3d4a4e",
    "integrations/binance_usdm/demo_read_routing.py": "a6e96b21135882a272cb5c97f86ddc14c3a5f4fa79dd115e40397252c679a66d",
    "integrations/binance_usdm/demo_snapshot.py": "02e5d789280f3ed8aa2667319248982d5cd4eedd2fb8b72e70aef8785e57ed6a",
    "integrations/binance_usdm/filters.py": "0279613f1a17a2bc7ffd50e30be2b6fd1aed8ba240219e8e893b754a067d95ec",
    "integrations/binance_usdm/orders.py": "2ab366265f7d35356ee9636c6aad4b594b518beeb636407515a57386b3e8441f",
    "integrations/binance_usdm/recovery_transport.py": "7495c0436437070644627d5ee175d7a63332dc5dc2134574285a7870e8bbba77",
    "integrations/binance_usdm/rest.py": "9a14edc40fa6365a50fe57c9ddeff4e866044e00287e7c5b680d8bb3dbd0ad52",
    "packages/domain/config.py": "c2bd7a039e3ea007a1e34a6dbdd058cc626e2690cc8d55b0761cfd2085bf311d",
    "packages/domain/contracts.py": "71c0a95faad586d6e3f342d83b6b95a4772cb6e2c1808b44fc51fdf9f1332947",
    "packages/persistence/demo.py": "40f9a33b8eeda28c96f36de35656e4667a2d2461ce9de79dc4b19a687cd5cd5a",
    "scripts/check_demo_tp_trigger.py": "e9a04c8b0dd877c8cdb1708ee2b75714984ab9c6837d873c067dc0a94e6c2baa",
    "scripts/demo_key_session.py": "85c698bc8bbad98bad4051c346a5e4e0a3a3bd22f8bbb90a362f835cadc633dd",
    "services/execution/demo.py": "deb90dd8ddd9df627054a11c76f8dd8b09e364f2aaaca8f82b7fd0c237c444ab",
    "services/execution/demo_empty_db_restart.py": "6adcc8284b43050bc4a115f2796c001d424ba7e0261654c099075fb3d950e39f",
    "services/execution/demo_event_clock.py": "924f3b88c9092355bb4b4ada3114944848208ab5b44ab73ee5b45d5fc1a29bfb",
    "services/execution/demo_event_inbox.py": "6d9be8c64e144592a5c2343c1db6e4dfc1dee65aa65eeebc08bd52b5aa633281",
    "services/execution/demo_flat_transport.py": "93f878c9869ad397a52546f0aa976bbe8071366eff36c7d0be6b1329df757401",
    "services/execution/demo_models.py": "175def10d7378661e82616b02ec87d6dbee4ffdeb4eb94437ce682206168dfb0",
    "services/execution/demo_protected_worker_check.py": "70d9f0a6ec74223e8a7a82ec55c976288c10fb13c01c9b478f49f1a0fd408709",
    "services/execution/demo_recovery.py": "ca663f098d2f7c462d1f225f4ade91d743169175f125c574794dc58175c8c76e",
    "services/execution/demo_recovery_budget.py": "2c7aa260fd708948b0ae7854257de03bc8b59c86da85180ea5230b07e8df772b",
    "services/execution/demo_stream.py": "770b0783287dcd607c35dad96b8882acd2bcb6a205928046b9831f9920a6a29f",
    "services/execution/demo_tp_trigger_check.py": "f2a0cbac4fc33142fa63befb5aa38e1eb7a6ac2bb10018c0ea04290f7e310a6a",
    "services/execution/demo_validation.py": "54b7c41e679f69c78019fac81781fc8c43954375343ee35e40d43830251d111f",
    "services/execution/models.py": "5d362e33788a304e6f2f63f22868454b3804b103277a6fe4a1297c7e50e10a5d",
    "services/execution/paper.py": "1a847e50fc78d48846fadc9953fad07aed5bbdf83f1e30986d39fef0e0d9fc13",
    "services/risk/paper.py": "7afe00460abae04dda795ab0dba8b8ad84877fa25686342c1fbce3f12ac80832",
}


class Blocked(ValueError):
    pass


def require(ok, code):
    if not ok:
        raise Blocked(code)


def verify_sources(phase, observations=None):
    actual = (
        observations
        if observations is not None
        else {
            n: hashlib.sha256(Path(n).read_bytes()).hexdigest() if Path(n).is_file() else None
            for n in NEW_PINS
        }
    )
    require(actual == NEW_PINS or (phase == "pre" and actual == OLD_PINS), "SOURCE_SET_MISMATCH")


def local(store):
    with store.engine.connect() as conn:
        conn.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        conn.exec_driver_sql("SET LOCAL statement_timeout='3s'")
        row = (
            conn.execute(select(accounts).where(accounts.c.account_id == ACCOUNT)).mappings().one()
        )
    state = DemoLedger.model_validate(row["state"])
    require(row["owner"] is None, "ACTIVE_OWNER_BLOCKED")
    empty(state)
    require(state.income_since_ms is not None, "INITIALIZED_LEDGER_REQUIRED")
    order = state.orders.get(INCIDENT)
    require(
        order is not None
        and order.quantity == order.filled == Decimal("0.0007")
        and order.state.value == "FILLED"
        and order.position_state.value == "CLOSED"
        and not order.open_quantity
        and not order.actual_risk
        and not order.remaining
        and not order.protected
        and order.exit_id is None
        and order.exit_state == "NONE"
        and str(order.server_order_id) == "28668115919",
        "MANUAL_CLOSE_LOCAL_SCOPE_MISMATCH",
    )
    receipt = json.loads(state.operations.get(RECEIPT, "{}"))
    require(isinstance(receipt, dict), "MANUAL_CLOSE_RECEIPT_MISMATCH")
    try:
        net = Decimal(receipt.get("net_usdt", "NaN"))
    except (InvalidOperation, ValueError, TypeError):
        raise Blocked("MANUAL_CLOSE_RECEIPT_MISMATCH") from None
    require(
        receipt.get("incident") == INCIDENT
        and receipt.get("source") == "SIGNED_OPERATOR_MANUAL_CLOSE"
        and receipt.get("entry_order_id") == "28668115919"
        and receipt.get("manual_close_order_id") == "28668119491"
        and receipt.get("trade_ids") == ["554837511", "554838531"]
        and net == Decimal("-0.07032487"),
        "MANUAL_CLOSE_RECEIPT_MISMATCH",
    )
    require(
        state.operations.get(str(uuid5(UUID(INCIDENT), "tp-trigger-observation")))
        == "TP_TRIGGER:NOT_ACCEPTED",
        "FAILED_TRIAL_MARKER_CHANGED",
    )
    return row["owner"], row["fence"], row["lease_until"], state


def check(store, client, phase):
    verify_sources(phase)
    before = local(store)
    ledger = before[-1]
    require(
        not client.settings.demo_trading_enabled
        and client.settings.margin_mode == ledger.margin_mode == "ISOLATED"
        and client.settings.symbols == ledger.symbols == ("BTCUSDT",)
        and client.settings.policy == ledger.policy,
        "READ_ONLY_CONTEXT_REQUIRED",
    )
    require(
        local_period_status(store, ledger)["period_keys_match"] is True, "PERIOD_BASELINE_MISMATCH"
    )
    with recovery_connection(client) as pooled:
        reader = RecoveryReadClient(client)
        reader.sync_clock()
        first = operational_snapshot(reader, ledger, store.now())
        last = operational_snapshot(reader, ledger, store.now())
        require(
            first.fingerprint == last.fingerprint and first.transfers == last.transfers,
            "SNAPSHOT_CHANGED",
        )
        require(
            not any(last.positions.values())
            and not last.margin_used
            and not any(r["status"] in {"NEW", "PARTIALLY_FILLED"} for r in last.orders.values())
            and not any(r["algoStatus"] == "NEW" for r in last.algos.values()),
            "REMOTE_NOT_EMPTY",
        )
        require(
            last.transfers == ledger.income_seen and last.leverage.get("BTCUSDT") == 2,
            "TRANSFER_OR_SETTINGS_CHANGED",
        )
        _check_loss_gates(store, ledger, last.equity)
        require(
            local_period_status(store, ledger)["period_keys_match"] is True,
            "PERIOD_BASELINE_MISMATCH",
        )
        reader.budget.check()
        require(local(store) == before, "LOCAL_STATE_CHANGED")
        return {
            "tp_rate_install": "POSTCHECKED_KEEP_PAUSED"
            if phase == "post"
            else "PRECHECKED_KEEP_PAUSED",
            "risk_state": ledger.risk_state.value,
            "paused": True,
            "remote_empty_verified": True,
            "manual_close_receipt_preserved": True,
            "failed_trial_preserved": True,
            "database_changed": False,
            "order_writes": 0,
            "orders_enabled": False,
            "live_trading_enabled": False,
            "signed_queries": reader.budget.signed_queries,
            "read_diagnostic": pooled.diagnostics() if pooled else {},
            "rate_limit_diagnostic": getattr(client, "rate_limit_diagnostics", lambda: {})(),
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=["pre", "post"], required=True)
    args = parser.parse_args()
    engine = None
    try:
        require(
            os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "false"
            and os.getenv("TRADING_MODE", "PAPER").upper() != "LIVE",
            "LIVE_CONTEXT_FORBIDDEN",
        )
        verify_sources(args.phase)
        cfg = DemoSettings.model_validate(
            yaml.load(
                Path("configs/demo-isolated.example.yaml").read_text(), Loader=UniqueKeyLoader
            )
        ).model_copy(update={"demo_trading_enabled": False})
        client = DemoClient(cfg, credentials(prompt=False))
        engine = create_engine(
            database_url().get_secret_value(), connect_args={"connect_timeout": 2}
        )
        report = check(DemoStore(engine), client, args.phase)
        code = 0
    except Exception as error:
        report = {
            "tp_rate_install": "BLOCKED_KEEP_PAUSED_NO_AUTOMATIC_RETRY",
            "phase": args.phase,
            "failure": str(error) if isinstance(error, Blocked) else "READ_ONLY_CHECK_UNAVAILABLE",
            "database_changed": False,
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
