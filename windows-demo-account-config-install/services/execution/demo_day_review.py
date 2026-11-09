"""Read-only KST day-boundary evidence. A wallet candidate is never an equity baseline."""

import hashlib
import json
from collections import Counter
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal, DecimalException
from zoneinfo import ZoneInfo

from sqlalchemy import select

from integrations.binance_usdm.demo import DemoClient, DemoFailure
from integrations.binance_usdm.demo_snapshot import signed_number, snapshot
from packages.persistence.demo import DemoStore, events
from services.execution.demo_models import DemoLedger

INCOME_TYPES = frozenset(
    {
        "TRANSFER",
        "WELCOME_BONUS",
        "REALIZED_PNL",
        "FUNDING_FEE",
        "COMMISSION",
        "INSURANCE_CLEAR",
        "COMMISSION_REBATE",
        "API_REBATE",
    }
)


DAY_REVIEW_CODES = frozenset(
    [
        "ACCOUNT_CHANGED_DURING_REVIEW",
        "BOUNDARY_EVENT_SCAN_INCOMPLETE",
        "BOUNDARY_TIMESTAMP_AMBIGUOUS",
        "CONFLICTING_INCOME_ID",
        "CURRENT_EXPOSURE_OR_OPEN_ORDER",
        "DATABASE_CHANGED_DURING_REVIEW",
        "INCOME_PAGINATION_INCOMPLETE",
        "INCOME_WINDOW_CHANGED",
        "INVALID_ACCOUNT_SCOPE",
        "INVALID_BOUNDARY_EVENT",
        "INVALID_INCOME_ID_OR_TIME",
        "INVALID_INCOME_NUMBER",
        "INVALID_INCOME_RESPONSE",
        "INVALID_READ_ONLY_WINDOW",
        "NONPOSITIVE_WALLET_CANDIDATE",
        "READ_ONLY_PAUSED_ISOLATED_CONTEXT_REQUIRED",
        "UNSUPPORTED_INCOME_ASSET_OR_TYPE",
        "WALLET_BALANCE_MISSING",
    ]
)


class DayReviewBlocked(DemoFailure):
    def __init__(self, code: str):
        super().__init__(code if code in DAY_REVIEW_CODES else "DAY_EVIDENCE_BLOCKED")


def income_window(client: DemoClient, start_ms: int, end_ms: int) -> dict[str, dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    if client.settings.demo_trading_enabled or start_ms < 0 or end_ms < start_ms:
        raise DayReviewBlocked("INVALID_READ_ONLY_WINDOW")
    for page in range(1, 11):
        raw = client.request(
            "GET",
            "/fapi/v1/income",
            {
                "startTime": str(start_ms),
                "endTime": str(end_ms),
                "page": str(page),
                "limit": "1000",
            },
        )
        if not isinstance(raw, list) or len(raw) > 1000:
            raise DayReviewBlocked("INVALID_INCOME_RESPONSE")
        for item in raw:
            if (
                not isinstance(item, dict)
                or item.get("asset") != "USDT"
                or not isinstance(item.get("incomeType"), str)
                or item.get("incomeType") not in INCOME_TYPES
            ):
                raise DayReviewBlocked("UNSUPPORTED_INCOME_ASSET_OR_TYPE")
            stamp = item.get("time")
            identifier = item.get("tranId")
            if (
                type(stamp) is not int
                or not start_ms <= stamp <= end_ms
                or type(identifier) not in (int, str)
                or not str(identifier)
            ):
                raise DayReviewBlocked("INVALID_INCOME_ID_OR_TIME")
            if stamp == start_ms:
                raise DayReviewBlocked("BOUNDARY_TIMESTAMP_AMBIGUOUS")
            try:
                value = signed_number(item.get("income"))
            except (DemoFailure, DecimalException, ValueError, TypeError):
                raise DayReviewBlocked("INVALID_INCOME_NUMBER") from None
            key = str(item["incomeType"]) + ":" + str(identifier)
            parsed = {
                "type": str(item["incomeType"]),
                "income": str(value),
                "time": str(stamp),
                "symbol": str(item.get("symbol", "")),
            }
            if key in rows and rows[key] != parsed:
                raise DayReviewBlocked("CONFLICTING_INCOME_ID")
            rows[key] = parsed
        if len(raw) < 1000:
            return rows
    raise DayReviewBlocked("INCOME_PAGINATION_INCOMPLETE")


def exact_boundary_records(
    store: DemoStore, account_id: str, boundary: datetime, max_age: int
) -> int:
    if not account_id.startswith("DEMO-") or len(account_id) > 80:
        raise DayReviewBlocked("INVALID_ACCOUNT_SCOPE")
    with store.engine.connect() as connection:
        payloads = (
            connection.execute(
                select(events.c.payload)
                .where(
                    events.c.account_id == account_id,
                    events.c.as_of >= boundary,
                    events.c.as_of <= boundary + timedelta(seconds=max_age),
                )
                .limit(201)
            )
            .scalars()
            .all()
        )
    if len(payloads) > 200:
        raise DayReviewBlocked("BOUNDARY_EVENT_SCAN_INCOMPLETE")
    found = 0
    for payload in payloads:
        try:
            recorded = DemoLedger.model_validate_json(payload)
        except ValueError:
            raise DayReviewBlocked("INVALID_BOUNDARY_EVENT") from None
        if (
            recorded.value.as_of == boundary
            and recorded.last_reconciled == boundary
            and recorded.reconciled
        ):
            found += 1
    return found


def review_day(
    store: DemoStore,
    client: DemoClient,
    ledger: DemoLedger,
    *,
    account_id: str = "DEMO-WINDOWS-ISOLATED-BTC-V1",
) -> dict[str, object]:
    if (
        client.settings.demo_trading_enabled
        or client.settings.margin_mode != "ISOLATED"
        or client.settings.symbols != ("BTCUSDT",)
        or ledger.margin_mode != "ISOLATED"
        or ledger.symbols != ("BTCUSDT",)
        or not ledger.paused
        or ledger.risk_state.value != "EMERGENCY"
    ):
        raise DayReviewBlocked("READ_ONLY_PAUSED_ISOLATED_CONTEXT_REQUIRED")
    now = store.now()
    day = now.astimezone(ZoneInfo("Asia/Seoul")).date()
    boundary = datetime.combine(day, time(), ZoneInfo("Asia/Seoul"))
    records = exact_boundary_records(
        store, account_id, boundary, ledger.policy.max_snapshot_age_seconds
    )
    start_ms = int(boundary.timestamp() * 1000)
    first = snapshot(client, ledger, store.now())
    end_ms = int(store.now().timestamp() * 1000)
    incomes = income_window(client, start_ms, end_ms)
    repeated = income_window(client, start_ms, end_ms)
    if incomes != repeated:
        raise DayReviewBlocked("INCOME_WINDOW_CHANGED")
    second = snapshot(client, ledger, store.now())
    if (
        second.as_of.astimezone(ZoneInfo("Asia/Seoul")).date() != day
        or first.fingerprint != second.fingerprint
    ):
        raise DayReviewBlocked("ACCOUNT_CHANGED_DURING_REVIEW")
    if (
        any(second.positions.values())
        or any(r["status"] in {"NEW", "PARTIALLY_FILLED"} for r in second.orders.values())
        or any(r["algoStatus"] == "NEW" for r in second.algos.values())
    ):
        raise DayReviewBlocked("CURRENT_EXPOSURE_OR_OPEN_ORDER")
    if second.wallet_balance is None:
        raise DayReviewBlocked("WALLET_BALANCE_MISSING")
    net = sum((Decimal(r["income"]) for r in incomes.values()), Decimal(0))
    net_transfers = sum(
        (
            Decimal(r["income"])
            for r in incomes.values()
            if r["type"] in {"TRANSFER", "WELCOME_BONUS"}
        ),
        Decimal(0),
    )
    candidate = second.wallet_balance - net
    if candidate <= 0:
        raise DayReviewBlocked("NONPOSITIVE_WALLET_CANDIDATE")
    evidence_hash = hashlib.sha256(
        json.dumps(incomes, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "review": "DAY_BOUNDARY_EVIDENCE_ONLY",
        "day_kst": day.isoformat(),
        "boundary_kst": boundary.isoformat(),
        "cutoff_utc": (
            datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=end_ms)
        ).isoformat(),
        "cutoff_ms": end_ms,
        "stored_day_kst": ledger.daily_key,
        "current_wallet_usdt": str(second.wallet_balance),
        "income_net_usdt": str(net),
        "transfer_and_bonus_net_usdt": str(net_transfers),
        "income_counts": dict(Counter(r["type"] for r in incomes.values())),
        "wallet_candidate_usdt_not_approved": str(candidate),
        "income_evidence_hash": evidence_hash,
        "exact_boundary_record_count": records,
        "boundary_unrealized_pnl_verified": False,
        "boundary_net_deposit_basis_verified": False,
        "recovery_eligible": False,
        "reason": "MIDNIGHT_EQUITY_AND_TRANSFER_BASIS_NOT_VERIFIED",
        "database_changed": False,
        "orders_enabled": False,
        "live_trading_enabled": False,
    }
