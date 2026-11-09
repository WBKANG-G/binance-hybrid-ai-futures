"""Readiness-only view for the explicitly reviewed external-close receipt.

No global parser/coordinator is changed. Invalid/incomplete receipts retain the
original history lookups. Fresh global checks and the normal snapshot pair are
still performed by exchange_digest on every call; no remote response is cached.
"""

import json
import re
from datetime import datetime, timedelta
from decimal import Decimal, DecimalException
from uuid import UUID, uuid5

from integrations.binance_usdm.demo import DemoSettings
from services.execution.demo_empty_db_restart import DBRestartBlocked, empty
from services.execution.demo_models import DemoLedger
from services.execution.models import PaperOrder

INCIDENT = "041d1f35-870e-46cb-aad2-ed6059bc1095"
RECEIPT = str(uuid5(UUID(INCIDENT), "signed-operator-manual-close-v1"))


def _matches(ledger: DemoLedger, now: datetime) -> bool:
    current = ledger.orders.get(INCIDENT)
    if (
        current is None
        or current.signal.signal_id != UUID(INCIDENT)
        or current.signal.environment.value != "DEMO"
        or ledger.income_since_ms is None
        or not 0 <= ledger.income_since_ms <= int(ledger.value.as_of.timestamp() * 1000)
        or ledger.operations.get(str(uuid5(UUID(INCIDENT), "tp-trigger-observation")))
        != "TP_TRIGGER:NOT_ACCEPTED"
        or current.quantity != current.filled
        or current.filled != Decimal("0.0007")
        or current.state.value != "FILLED"
        or current.exit_id is not None
        or current.exit_state != "NONE"
        or str(current.server_order_id) != "28668115919"
        or not current.algo_id
        or not current.tp_algo_id
        or current.algo_id == current.tp_algo_id
        or current.signal.symbol != "BTCUSDT"
        or current.signal.direction != "LONG"
        or current.signal.purpose != "DEMO_VALIDATION"
    ):
        return False
    raw = json.loads(ledger.operations.get(RECEIPT, "{}"))
    if not isinstance(raw, dict) or not (
        raw.get("incident") == INCIDENT
        and raw.get("source") == "SIGNED_OPERATOR_MANUAL_CLOSE"
        and raw.get("entry_order_id") == "28668115919"
        and raw.get("manual_close_order_id") == "28668119491"
        and raw.get("trade_ids") == ["554837511", "554838531"]
        and Decimal(raw.get("net_usdt", "NaN")) == Decimal("-0.07032487")
        and isinstance(raw.get("manual_close_client_id"), str)
        and raw["manual_close_client_id"].startswith("web_")
        and raw["manual_close_client_id"] not in ledger.requests
        and all(
            isinstance(raw.get(k), str) and re.fullmatch("[0-9a-f]{64}", raw[k])
            for k in ("exchange_fingerprint", "prior_ledger_sha256")
        )
    ):
        return False
    original = PaperOrder.model_validate(raw.get("original_order"))
    allowed = {"open_quantity", "actual_risk", "protected", "position_state"}
    before, after = original.model_dump(mode="json"), current.model_dump(mode="json")
    if not (
        all(before[k] == after[k] for k in before.keys() - allowed)
        and original.state.value == "FILLED"
        and original.position_state.value == "OPEN_UNPROTECTED"
        and original.open_quantity == original.quantity == original.filled == Decimal("0.0007")
        and not original.remaining
        and not original.protected
        and original.exit_id is None
        and original.exit_state == "NONE"
    ):
        return False
    stamp = datetime.fromisoformat(raw["acknowledged_at_utc"])
    return (
        stamp.tzinfo is not None
        and stamp.utcoffset() == timedelta(0)
        and ledger.value.as_of <= stamp <= now
    )


def readiness_view(
    settings: DemoSettings, ledger: DemoLedger, now: datetime
) -> tuple[DemoLedger, int]:
    """Only the read-only readiness CLI calls this function; preserve all DB state."""
    if (
        settings.demo_trading_enabled
        or settings.margin_mode != ledger.margin_mode
        or settings.margin_mode != "ISOLATED"
        or settings.symbols != ledger.symbols
        or settings.symbols != ("BTCUSDT",)
        or settings.policy != ledger.policy
    ):
        return ledger, 0
    try:
        empty(ledger)
        if not _matches(ledger, now):
            return ledger, 0
    except (DBRestartBlocked, ValueError, TypeError, KeyError, DecimalException):
        # No new proof means no optimization. Original strict reads still run.
        return ledger, 0
    view = ledger.model_copy(deep=True)
    del view.orders[INCIDENT]
    return view, 1
