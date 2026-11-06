"""Manual-source day review when signed exit predates midnight but REST merge does not.

The observation timestamp is never moved backwards. Signed BTC fills and USDT
income corroborate an explicit operator statement, not exchange-wide history proof.
"""

import hashlib
import json
import re
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from typing import Final
from uuid import UUID
from zoneinfo import ZoneInfo

from integrations.binance_usdm.demo import DemoClient, DemoFailure
from integrations.binance_usdm.demo_snapshot import signed_number, snapshot
from packages.domain.contracts import OrderState, PositionState, RiskState
from packages.persistence.demo import DemoStore
from services.execution.demo_day_review import income_window
from services.execution.demo_history_review import trades_window
from services.execution.demo_models import DemoLedger, DemoSnapshot, ExitAnchoredDayBaseline
from services.execution.models import Lease
from services.execution.paper import account_risk
from services.risk.paper import AccountValue, period_keys

SOURCE: Final = "OPERATOR_ATTESTED_PRE_MIDNIGHT_EXIT_WITH_SIGNED_USDT_EVIDENCE"
OPERATION = "ADOPT_EXIT_ANCHORED_OPERATOR_DAY_BASELINE"
TERMINAL_ALGOS = {"CANCELED", "EXPIRED", "FINISHED", "REJECTED"}


class ExitDayBlocked(DemoFailure):
    pass


def known_exit(
    ledger: DemoLedger, remote: DemoSnapshot, trades: dict[str, dict[str, str]]
) -> tuple[int, set[str]]:
    exits: list[int] = []
    exit_ids: set[str] = set()
    for order in ledger.orders.values():
        if not order.filled:
            continue
        entry = remote.orders.get(order.client_id)
        exit_row = remote.orders.get(order.exit_id or "")
        if (
            order.position_state != PositionState.CLOSED
            or order.exit_state != "FILLED"
            or not entry
            or entry["orderId"] != order.server_order_id
            or entry["clientOrderId"] != order.client_id
            or entry["symbol"] != "BTCUSDT"
            or entry["positionSide"] != "BOTH"
            or entry["reduceOnly"] != "false"
            or entry["type"] != "LIMIT"
            or entry["side"] != ("BUY" if order.signal.direction == "LONG" else "SELL")
            or signed_number(entry["origQty"]) != order.quantity
            or signed_number(entry["executedQty"]) != order.filled
            or not exit_row
            or exit_row["status"] != "FILLED"
            or exit_row["clientOrderId"] != order.exit_id
            or exit_row["symbol"] != "BTCUSDT"
            or exit_row["reduceOnly"] != "true"
            or exit_row["positionSide"] != "BOTH"
            or exit_row["type"] != "MARKET"
            or exit_row["side"] != ("SELL" if order.signal.direction == "LONG" else "BUY")
            or signed_number(exit_row["executedQty"]) != order.filled
            or signed_number(exit_row["origQty"]) != order.filled
        ):
            raise ExitDayBlocked("KNOWN_ENTRY_OR_EXIT_NOT_CONFIRMED")
        for raw, side in (
            (entry, "BUY" if order.signal.direction == "LONG" else "SELL"),
            (exit_row, "SELL" if order.signal.direction == "LONG" else "BUY"),
        ):
            matched = [r for r in trades.values() if r["order_id"] == raw["orderId"]]
            if (
                not matched
                or sum((Decimal(r["quantity"]) for r in matched), Decimal(0)) != order.filled
                or any(r["side"] != side for r in matched)
            ):
                raise ExitDayBlocked("KNOWN_FILL_HISTORY_NOT_CONFIRMED")
        closing = [r for r in trades.values() if r["order_id"] == exit_row["orderId"]]
        exits.append(max(int(r["time"]) for r in closing))
        exit_ids.add(exit_row["orderId"])
    if not exits:
        raise ExitDayBlocked("NO_KNOWN_FULL_EXIT")
    return max(exits), exit_ids


def review_exit_day(
    store: DemoStore,
    client: DemoClient,
    ledger: DemoLedger,
    *,
    account_id: str,
    attest_no_activity: bool,
) -> dict[str, object]:
    if not attest_no_activity:
        raise ExitDayBlocked("EXPLICIT_NO_ACTIVITY_ATTESTATION_REQUIRED")
    now = store.now()
    day, week = period_keys(now, ledger.policy.week_start_kst)
    boundary = datetime.combine(
        now.astimezone(ZoneInfo("Asia/Seoul")).date(), time(), ZoneInfo("Asia/Seoul")
    ).astimezone(UTC)
    observation = ledger.last_reconciled
    if (
        not account_id.startswith("DEMO-")
        or client.settings.demo_trading_enabled
        or client.settings.policy != ledger.policy
        or client.settings.margin_mode != "ISOLATED"
        or client.settings.symbols != ("BTCUSDT",)
        or ledger.margin_mode != "ISOLATED"
        or ledger.symbols != ("BTCUSDT",)
        or ledger.risk_state != RiskState.EMERGENCY
        or not ledger.paused
        or observation is None
        or ledger.value.as_of != observation
        or not boundary <= observation <= now
        or ledger.daily_key >= day
        or not ledger.orders
        or any(ledger.positions.values())
        or any(
            o.remaining
            or o.open_quantity
            or o.state
            not in {OrderState.FILLED, OrderState.CANCELED, OrderState.REJECTED, OrderState.EXPIRED}
            for o in ledger.orders.values()
        )
    ):
        raise ExitDayBlocked("EXIT_DAY_CONTEXT_MISMATCH")
    if ledger.weekly_key != week:
        raise ExitDayBlocked("WEEK_BASELINE_REQUIRES_SEPARATE_REVIEW")
    if (
        ledger.income_since_ms is None
        or not 0 <= ledger.income_since_ms <= int(observation.timestamp() * 1000)
        or ledger.income_seen
        or ledger.net_transfers != 0
        or ledger.value.net_deposits != 0
    ):
        raise ExitDayBlocked("NONZERO_OR_UNKNOWN_TRANSFER_BASIS")
    first = snapshot(client, ledger, now)
    start = min(o.signal.signal_closed_candle_at for o in ledger.orders.values())
    start_ms = int(start.timestamp() * 1000) - 1
    end_ms = int(now.timestamp() * 1000)
    trades = trades_window(client, start_ms, end_ms)
    incomes = income_window(client, start_ms, end_ms)
    if trades != trades_window(client, start_ms, end_ms) or incomes != income_window(
        client, start_ms, end_ms
    ):
        raise ExitDayBlocked("HISTORY_CHANGED")
    exit_ms, exit_ids = known_exit(ledger, first, trades)
    if not ledger.income_since_ms <= exit_ms < int(boundary.timestamp() * 1000):
        raise ExitDayBlocked("LATEST_EXIT_NOT_BEFORE_MIDNIGHT")
    if any(
        int(row["time"]) > exit_ms
        or (int(row["time"]) == exit_ms and row["order_id"] not in exit_ids)
        for row in trades.values()
    ):
        raise ExitDayBlocked("OTHER_BTC_ACTIVITY_AT_OR_AFTER_EXIT")
    # Income may be timestamped at the beginning of the fill's second. This is
    # an exact bucket match, not a +/- time tolerance. Any other BTC trade in
    # that bucket makes attribution ambiguous and must block this narrow path.
    second_ms = exit_ms // 1000 * 1000
    if any(int(r["time"]) >= second_ms and r["order_id"] not in exit_ids for r in trades.values()):
        raise ExitDayBlocked("OTHER_BTC_ACTIVITY_IN_EXIT_SECOND")
    closing = [r for r in trades.values() if int(r["time"]) >= second_ms]
    allowed_income_times = {second_ms, *(int(r["time"]) for r in closing)}
    tail = [r for r in incomes.values() if int(r["time"]) >= second_ms]
    if any(
        int(r["time"]) not in allowed_income_times
        or r["symbol"] != "BTCUSDT"
        or r["type"] not in {"COMMISSION", "REALIZED_PNL"}
        for r in tail
    ):
        raise ExitDayBlocked("POST_EXIT_INCOME_OR_FLOW")
    expected_fee = -sum((Decimal(r["commission"]) for r in closing), Decimal(0))
    expected_pnl = sum((Decimal(r["realized_pnl"]) for r in closing), Decimal(0))
    for kind, expected in (("COMMISSION", expected_fee), ("REALIZED_PNL", expected_pnl)):
        amounts = [Decimal(r["income"]) for r in tail if r["type"] == kind]
        expected_amounts = [
            -Decimal(r["commission"]) if kind == "COMMISSION" else Decimal(r["realized_pnl"])
            for r in closing
        ]
        if (
            sum(amounts, Decimal(0)) != expected
            or len(amounts) > len(closing)
            or sorted(v for v in amounts if v) != sorted(v for v in expected_amounts if v)
        ):
            raise ExitDayBlocked("EXIT_INCOME_DOES_NOT_MATCH_FILLS")
    raw = client.request("GET", "/fapi/v3/account")
    if not isinstance(raw, dict) or not isinstance(raw.get("assets"), list):
        raise ExitDayBlocked("USDT_ASSET_SCOPE_MISSING")
    usdt = [r for r in raw["assets"] if isinstance(r, dict) and r.get("asset") == "USDT"]
    if len(usdt) != 1:
        raise ExitDayBlocked("USDT_ASSET_SCOPE_MISSING")
    wallet = signed_number(usdt[0].get("walletBalance"))
    if (
        wallet <= 0
        or signed_number(usdt[0].get("unrealizedProfit")) != 0
        or signed_number(usdt[0].get("marginBalance")) != wallet
        or signed_number(raw.get("totalWalletBalance")) != wallet
        or signed_number(raw.get("totalMarginBalance")) != wallet
        or signed_number(raw.get("totalUnrealizedProfit")) != 0
    ):
        raise ExitDayBlocked("USDT_TOTAL_OR_UNREALIZED_MISMATCH")
    final = snapshot(client, ledger, store.now())
    if (
        first.fingerprint != final.fingerprint
        or first.wallet_balance != wallet
        or final.wallet_balance != wallet
        or ledger.value.equity != wallet
        or any(final.positions.values())
        or any(
            r["status"] not in {"FILLED", "CANCELED", "EXPIRED", "EXPIRED_IN_MATCH", "REJECTED"}
            for r in final.orders.values()
        )
        or any(r["algoStatus"] not in TERMINAL_ALGOS for r in final.algos.values())
        or final.leverage.get("BTCUSDT") != 2
        or period_keys(store.now(), ledger.policy.week_start_kst) != (day, week)
    ):
        raise ExitDayBlocked("ACCOUNT_OR_DAY_CHANGED")
    for base, cap in (
        (ledger.daily_base, "daily_loss_limit_pct"),
        (ledger.weekly_base, "weekly_loss_limit_pct"),
        (ledger.high_water, "max_drawdown_pct"),
    ):
        if (base - wallet) / base * 100 >= ledger.policy.limit(cap):
            raise ExitDayBlocked("EXISTING_LOSS_LIMIT_REACHED")
    if ledger.high_water < wallet:
        raise ExitDayBlocked("HIGH_WATER_REQUIRES_SEPARATE_REVIEW")
    probe = ledger.model_copy(deep=True)
    probe.daily_key, probe.daily_base = day, wallet
    probe.value = AccountValue(
        as_of=store.now(),
        initial=ledger.value.initial,
        realized=wallet - ledger.value.initial,
        net_deposits=Decimal(0),
    )
    probe.risk_state = RiskState.NORMAL
    account_risk(probe, store.now())
    if probe.risk_state != RiskState.NORMAL:
        raise ExitDayBlocked("RECONSTRUCTED_RISK_GATES_FAILED")
    evidence = hashlib.sha256(
        json.dumps(
            {"trades": trades, "incomes": incomes, "snapshot": final.fingerprint}, sort_keys=True
        ).encode()
    ).hexdigest()
    payload = {
        "account_id": account_id,
        "source": SOURCE,
        "boundary": boundary.isoformat(),
        "observation": observation.isoformat(),
        "exit_ms": exit_ms,
        "ledger": ledger.model_dump(mode="json", exclude={"reconciled", "stream_ready"}),
        "signed_evidence": evidence,
        "candidate": str(wallet),
        "income_time_basis": "SECOND_BUCKET_OR_EXACT_FILL_TIMESTAMP",
    }
    token = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "review": "EXIT_ANCHORED_DAY_PREVIEW_NO_DATABASE_CHANGE",
        "account_id": account_id,
        "source": SOURCE,
        "review_token": token,
        "old_daily_key": ledger.daily_key,
        "old_daily_base_usdt": str(ledger.daily_base),
        "proposed_day_kst": day,
        "boundary_utc": boundary.isoformat(),
        "proposed_daily_base_usdt": str(wallet),
        "net_deposit_basis_usdt": "0",
        "observation_utc": observation.isoformat(),
        "known_exit_utc": (
            datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=exit_ms)
        ).isoformat(),
        "attested_until_utc": store.now().isoformat(),
        "signed_evidence_hash": evidence,
        "exchange_fingerprint": final.fingerprint,
        "post_exit_btc_trades": 0,
        "post_exit_income_records": 0,
        "income_time_basis": "SECOND_BUCKET_OR_EXACT_FILL_TIMESTAMP",
        "exit_income_second_start_utc": (
            datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=second_ms)
        ).isoformat(),
        "account_wide_exchange_export_verified": False,
        "operator_attestation_used": True,
        "weekly_base_preserved": True,
        "high_water_preserved": True,
        "old_valuation_timestamp_preserved": True,
        "requests_preserved": True,
        "risk_state": ledger.risk_state.value,
        "paused": ledger.paused,
        "database_changed": False,
        "orders_enabled": False,
        "live_trading_enabled": False,
        "recovery_eligible": False,
    }


def apply_exit_day(
    store: DemoStore,
    lease: Lease,
    client: DemoClient,
    operation_id: UUID,
    expected_token: str,
    *,
    attest_no_activity: bool,
    accept_operator_evidence: bool,
) -> str:
    if (
        not attest_no_activity
        or not accept_operator_evidence
        or not re.fullmatch(r"[0-9a-f]{64}", expected_token)
        or client.settings.demo_trading_enabled
    ):
        raise ExitDayBlocked("EXPLICIT_READ_ONLY_MANUAL_SOURCE_ACCEPTANCE_REQUIRED")

    def apply(ledger: DemoLedger) -> str:
        key = str(operation_id)
        receipt = ledger.exit_anchored_day_baselines.get(key)
        if receipt is not None:
            if receipt.review_token != expected_token or ledger.operations.get(key) != OPERATION:
                raise ExitDayBlocked("DAY_OPERATION_CONFLICT")
            return "ALREADY_RECORDED_NO_ACTION"
        if (
            key in ledger.operations
            or key in ledger.recovery_receipts
            or key in ledger.cancelled_exposure_corrections
            or key in ledger.attested_day_baselines
        ):
            raise ExitDayBlocked("DAY_OPERATION_CONFLICT")
        report = review_exit_day(
            store,
            client,
            ledger,
            account_id=lease.account_id,
            attest_no_activity=attest_no_activity,
        )
        if report["review_token"] != expected_token:
            raise ExitDayBlocked("DAY_PREVIEW_CHANGED")
        receipt = ExitAnchoredDayBaseline(
            review_token=expected_token,
            source=SOURCE,
            boundary_utc=datetime.fromisoformat(str(report["boundary_utc"])),
            baseline_usdt=Decimal(str(report["proposed_daily_base_usdt"])),
            old_daily_key=ledger.daily_key,
            old_daily_base=ledger.daily_base,
            observation_utc=datetime.fromisoformat(str(report["observation_utc"])),
            known_exit_utc=datetime.fromisoformat(str(report["known_exit_utc"])),
            attested_until_utc=store.now(),
            signed_evidence_hash=str(report["signed_evidence_hash"]),
            exchange_fingerprint=str(report["exchange_fingerprint"]),
            income_time_basis="SECOND_BUCKET_OR_EXACT_FILL_TIMESTAMP",
        )
        ledger.daily_key, ledger.daily_base = str(report["proposed_day_kst"]), receipt.baseline_usdt
        ledger.exit_anchored_day_baselines[key] = receipt
        ledger.operations[key] = OPERATION
        ledger.reconciled = ledger.stream_ready = False
        return "EXIT_ANCHORED_DAILY_BASELINE_ADOPTED_KEEP_LOCK"

    return store.change(lease, "DEMO_OPERATOR_EXIT_ANCHORED_DAY_BASELINE", apply)
