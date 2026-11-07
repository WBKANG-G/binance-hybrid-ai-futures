"""Allowlisted private-stream diagnostics; never include raw events, URLs or errors."""

from typing import Literal, cast

from sqlalchemy.exc import SQLAlchemyError
from websockets.exceptions import ConnectionClosed

from integrations.binance_usdm.demo import DemoFailure, DemoRateLimited
from services.execution.demo_models import PrivateSessionDiagnostic
from services.risk.paper import RiskRejected

EVENT_KINDS = frozenset(
    {
        "ORDER_TRADE_UPDATE",
        "ALGO_UPDATE",
        "ACCOUNT_UPDATE",
        "ACCOUNT_CONFIG_UPDATE",
        "MARGIN_CALL",
        "listenKeyExpired",
        "TRADE_LITE",
    }
)
type EventKind = Literal[
    "NONE",
    "UNKNOWN",
    "ORDER_TRADE_UPDATE",
    "ALGO_UPDATE",
    "ACCOUNT_UPDATE",
    "ACCOUNT_CONFIG_UPDATE",
    "MARGIN_CALL",
    "listenKeyExpired",
    "TRADE_LITE",
]


def event_info(raw: dict[str, object], now_ms: int) -> tuple[EventKind, int | None]:
    kind = raw.get("e")
    safe = cast(EventKind, kind) if isinstance(kind, str) and kind in EVENT_KINDS else "UNKNOWN"
    stamp = raw.get("E")
    age = max(-60000, min(3600000, now_ms - stamp)) if type(stamp) is int else None
    return safe, age


def failure_diagnostic(
    error: Exception, detail: PrivateSessionDiagnostic
) -> PrivateSessionDiagnostic:
    code: str = "OTHER_SESSION_FAILURE"
    if isinstance(error, ConnectionClosed):
        code = "SOCKET_CLOSED"
    elif isinstance(error, TimeoutError):
        code = "TIMEOUT"
    elif isinstance(error, DemoRateLimited):
        code = "RATE_LIMITED"
    elif isinstance(error, RiskRejected):
        code = "LEASE_OR_RISK"
    elif isinstance(error, SQLAlchemyError):
        state = getattr(getattr(error, "orig", None), "sqlstate", None)
        code = {
            "55P03": "DB_LOCK_TIMEOUT",
            "40P01": "DB_DEADLOCK",
            "08000": "DB_CONNECTION",
            "08001": "DB_CONNECTION",
            "08003": "DB_CONNECTION",
            "08004": "DB_CONNECTION",
            "08006": "DB_CONNECTION",
            "08007": "DB_CONNECTION",
            "08P01": "DB_CONNECTION",
        }.get(state if isinstance(state, str) else "", "OTHER_DATABASE")
    elif isinstance(error, DemoFailure):
        code = {
            "Unrecognized DEMO private event": "EVENT_UNKNOWN",
            "DEMO event clock unavailable or untrusted": "EVENT_CLOCK_UNTRUSTED",
            "DEMO event clock validation interrupted": "EVENT_CLOCK_INCOMPLETE",
            "DEMO private event inbox overflow": "EVENT_BUFFER_OVERFLOW",
            "DEMO private listen key expired": "LISTEN_KEY_EXPIRED",
            "Stale/future DEMO private event": "EVENT_TIME_INVALID",
            "DEMO private event oversized": "EVENT_OVERSIZED",
            "Invalid DEMO event object": "EVENT_OBJECT_INVALID",
            "DEMO REST snapshots changed mid-reconciliation": "SNAPSHOT_CHANGED",
            "Invalid/incomplete DEMO snapshot": "SNAPSHOT_INVALID",
            "Clock stale or cooldown active": "CLOCK_OR_COOLDOWN",
        }.get(str(error), "DEMO_QUERY_OR_SESSION")
    elif detail.stage == "PARSE" and isinstance(error, (ValueError, UnicodeError)):
        code = "EVENT_JSON_INVALID"
    # Validate even internally constructed values; never admit exception text.
    return PrivateSessionDiagnostic.model_validate(detail.model_dump() | {"code": code})
