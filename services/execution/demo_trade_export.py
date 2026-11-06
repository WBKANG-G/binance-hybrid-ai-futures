"""One explicitly requested account-wide export job, never a baseline or unlock.

The official connector documents five trade-export creations/month (UI and API
shared). A local intent is durably recorded BEFORE GET /trade/asyn. Any ambiguous
outcome blocks regeneration; status uses only the already recorded download ID.
"""

import hashlib
import json
import os
import re
import stat
import tempfile
from datetime import UTC, datetime, time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from integrations.binance_usdm.demo import DemoClient, DemoFailure
from integrations.binance_usdm.demo_snapshot import signed_number, snapshot
from packages.domain.contracts import OrderState
from packages.persistence.demo import DemoStore
from services.execution.demo_models import DemoLedger


class ExportBlocked(DemoFailure):
    pass


def plan(ledger: DemoLedger, now: datetime, account_id: str) -> dict[str, Any]:
    if (
        not account_id.startswith("DEMO-")
        or len(account_id) > 80
        or not ledger.paused
        or ledger.risk_state.value != "EMERGENCY"
        or ledger.margin_mode != "ISOLATED"
        or ledger.symbols != ("BTCUSDT",)
        or ledger.income_since_ms is None
    ):
        raise ExportBlocked("EXPORT_CONTEXT_MISMATCH")
    if not 0 <= ledger.income_since_ms <= int(now.timestamp() * 1000):
        raise ExportBlocked("EXPORT_WINDOW_OUT_OF_BOUNDS")
    tracking = datetime.fromtimestamp(ledger.income_since_ms / 1000, UTC)
    start = datetime.combine(
        tracking.astimezone(ZoneInfo("Asia/Seoul")).date(), time(), ZoneInfo("Asia/Seoul")
    )
    start_ms, end_ms = int(start.timestamp() * 1000), int(now.timestamp() * 1000)
    if not 0 <= end_ms - start_ms <= 365 * 86400000:
        raise ExportBlocked("EXPORT_WINDOW_OUT_OF_BOUNDS")
    identity = {
        key: {
            "client_id": order.client_id,
            "server_id": order.server_order_id,
            "symbol": order.signal.symbol,
            "quantity": str(order.quantity),
        }
        for key, order in ledger.orders.items()
    }
    if not 1 <= len(identity) <= 30:
        raise ExportBlocked("EXPORT_ACCOUNT_IDENTITY_MISSING")
    digest = hashlib.sha256(
        json.dumps({"account_id": account_id, "orders": identity}, sort_keys=True).encode()
    ).hexdigest()
    return {
        "account_id": account_id,
        "account_binding": digest,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "start_kst": start.isoformat(),
        "end_utc": now.astimezone(UTC).isoformat(),
    }


def _private_directory(path: Path) -> None:
    try:
        path.mkdir(mode=0o700, exist_ok=True)
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ExportBlocked("EXPORT_JOURNAL_DIRECTORY_NOT_PRIVATE")
    except OSError:
        raise ExportBlocked("EXPORT_JOURNAL_IO_FAILED") from None


def _read(path: Path) -> dict[str, Any]:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "r") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ExportBlocked("EXPORT_JOURNAL_NOT_PRIVATE")
            raw = stream.read(16385)
        result = json.loads(raw)
        if (
            len(raw) > 16384
            or not isinstance(result, dict)
            or type(result.get("version")) is not int
            or result["version"] != 1
        ):
            raise ValueError("Invalid journal")
        UUID(result["request_id"])
        if (
            not isinstance(result.get("account_id"), str)
            or not isinstance(result.get("account_binding"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", result["account_binding"])
            or type(result.get("start_ms")) is not int
            or type(result.get("end_ms")) is not int
            or result["start_ms"] < 0
            or not 0 <= result["end_ms"] - result["start_ms"] <= 365 * 86400000
            or result.get("phase") not in {"INTENT_RECORDED", "JOB_REQUESTED", "LINK_READY"}
        ):
            raise ValueError("Invalid journal")
        if result["phase"] != "INTENT_RECORDED":
            _identifier(result.get("download_id"))
        return result
    except (OSError, ValueError, UnicodeError, KeyError, TypeError, AttributeError):
        raise ExportBlocked("EXPORT_JOURNAL_INVALID_DO_NOT_RECREATE") from None


def _sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _save(path: Path, record: dict[str, Any]) -> None:
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
            temp_name = stream.name
            json.dump(record, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
        temp_name = None
        _sync_directory(path.parent)
    except OSError:
        raise ExportBlocked("EXPORT_JOURNAL_IO_FAILED_KEEP_INTENT") from None
    finally:
        if temp_name is not None:
            Path(temp_name).unlink(missing_ok=True)


def _preflight(store: DemoStore, client: DemoClient, ledger: DemoLedger) -> None:
    if (
        client.settings.demo_trading_enabled
        or client.settings.policy != ledger.policy
        or client.settings.symbols != ledger.symbols
        or client.settings.margin_mode != ledger.margin_mode
    ):
        raise ExportBlocked("EXPORT_READ_ONLY_CLIENT_REQUIRED")
    if any(ledger.positions.values()) or any(
        o.open_quantity
        or o.remaining
        or o.state
        not in {OrderState.FILLED, OrderState.CANCELED, OrderState.REJECTED, OrderState.EXPIRED}
        for o in ledger.orders.values()
    ):
        raise ExportBlocked("EXPORT_LOCAL_EXPOSURE_OR_PENDING_ORDER")
    first = snapshot(client, ledger, store.now())
    second = snapshot(client, ledger, store.now())
    if first.fingerprint != second.fingerprint:
        raise ExportBlocked("EXPORT_ACCOUNT_CHANGED")
    if (
        any(second.positions.values())
        or any(r["status"] in {"NEW", "PARTIALLY_FILLED"} for r in second.orders.values())
        or any(r["algoStatus"] == "NEW" for r in second.algos.values())
        or second.leverage.get("BTCUSDT") != 2
    ):
        raise ExportBlocked("EXPORT_CURRENT_EXPOSURE_OR_SETTINGS_MISMATCH")
    for order in ledger.orders.values():
        row = second.orders.get(order.client_id)
        if (
            row is None
            or row["clientOrderId"] != order.client_id
            or order.server_order_id is None
            or row["orderId"] != order.server_order_id
            or row["symbol"] != order.signal.symbol
            or row["positionSide"] != "BOTH"
            or row["side"] != ("BUY" if order.signal.direction == "LONG" else "SELL")
            or row["type"] != "LIMIT"
            or row["reduceOnly"] != "false"
            or row["status"] != order.state.value
            or signed_number(row["executedQty"]) != order.filled
            or signed_number(row["origQty"]) != order.quantity
            or signed_number(row["price"]) != order.signal.suggested_limit_price
        ):
            raise ExportBlocked("EXPORT_ORIGINAL_ACCOUNT_ORDERS_NOT_CONFIRMED")


def _identifier(value: object) -> str:
    if type(value) not in (str, int) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", str(value)
    ):
        raise ExportBlocked("EXPORT_INVALID_DOWNLOAD_ID_KEEP_INTENT")
    return str(value)


def _bound(record: dict[str, Any], proposed: dict[str, Any]) -> None:
    if (
        record.get("account_binding") != proposed["account_binding"]
        or record.get("account_id") != proposed["account_id"]
    ):
        raise ExportBlocked("EXPORT_JOURNAL_ACCOUNT_MISMATCH")


def _public(record: dict[str, Any], status: str) -> dict[str, Any]:
    return {
        "export": status,
        "request_id": record.get("request_id"),
        "download_id": record.get("download_id"),
        "start_ms": record.get("start_ms"),
        "end_ms": record.get("end_ms"),
        "trade_scope": "ACCOUNT_WIDE_EXPORT_REQUEST",
        "file_downloaded": False,
        "historical_completeness_verified": False,
        "database_changed": False,
        "recovery_eligible": False,
        "orders_enabled": False,
        "live_trading_enabled": False,
    }


def request_export(
    store: DemoStore, client: DemoClient, ledger: DemoLedger, account_id: str, journal: Path
) -> dict[str, Any]:
    if client.settings.demo_trading_enabled:
        raise ExportBlocked("EXPORT_READ_ONLY_CLIENT_REQUIRED")
    proposed = plan(ledger, store.now(), account_id)
    _private_directory(journal.parent)
    if journal.exists() or journal.is_symlink():
        previous = _read(journal)
        _bound(previous, proposed)
        if previous.get("download_id") is not None:
            _identifier(previous["download_id"])
            return _public(previous, "ALREADY_REQUESTED_NO_NEW_JOB")
        raise ExportBlocked("EXPORT_INTENT_EXISTS_DO_NOT_REPEAT")
    _preflight(store, client, ledger)
    record = {
        "version": 1,
        "request_id": str(uuid4()),
        **proposed,
        "phase": "INTENT_RECORDED",
        "download_id": None,
    }
    try:
        fd = os.open(journal, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(record, stream)
            stream.flush()
            os.fsync(stream.fileno())
        _sync_directory(journal.parent)
    except OSError:
        raise ExportBlocked("EXPORT_INTENT_NOT_EXCLUSIVELY_RECORDED") from None
    try:
        raw = client.request(
            "GET",
            "/fapi/v1/trade/asyn",
            {"startTime": str(record["start_ms"]), "endTime": str(record["end_ms"])},
        )
        if not isinstance(raw, dict):
            raise ExportBlocked("EXPORT_INVALID_RESPONSE_KEEP_INTENT")
        record["download_id"] = _identifier(raw.get("downloadId"))
    except DemoFailure:
        # The durable intent remains. No resend even after a timeout or an
        # unavailable DEMO endpoint; do not classify failure as 'never requested'.
        raise ExportBlocked("EXPORT_REQUEST_UNRESOLVED_DO_NOT_REPEAT") from None
    record["phase"] = "JOB_REQUESTED"
    _save(journal, record)
    return _public(record, "JOB_REQUESTED_NOT_DOWNLOADED")


def status_export(
    store: DemoStore, client: DemoClient, ledger: DemoLedger, account_id: str, journal: Path
) -> dict[str, Any]:
    if client.settings.demo_trading_enabled:
        raise ExportBlocked("EXPORT_READ_ONLY_CLIENT_REQUIRED")
    proposed = plan(ledger, store.now(), account_id)
    _private_directory(journal.parent)
    record = _read(journal)
    _bound(record, proposed)
    identifier = _identifier(record.get("download_id"))
    _preflight(store, client, ledger)
    try:
        raw = client.request("GET", "/fapi/v1/trade/asyn/id", {"downloadId": identifier})
        if (
            not isinstance(raw, dict)
            or _identifier(raw.get("downloadId")) != identifier
            or not isinstance(raw.get("status"), str)
        ):
            raise ExportBlocked("EXPORT_INVALID_STATUS_RESPONSE")
        status = raw["status"].lower()
        if status not in {"processing", "completed", "failed"}:
            raise ExportBlocked("EXPORT_UNKNOWN_STATUS")
        result = _public(
            record, "LINK_READY_NOT_DOWNLOADED" if status == "completed" else status.upper()
        )
        if status == "completed":
            url = raw.get("url")
            if not isinstance(url, str) or len(url) > 8192 or any(ord(c) < 33 for c in url):
                raise ExportBlocked("EXPORT_INVALID_LINK")
            parsed = urlsplit(url)
            host = parsed.hostname
            if (
                parsed.scheme != "https"
                or not host
                or not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", host)
                or parsed.username
                or parsed.password
                or parsed.port not in (None, 443)
                or parsed.fragment
            ):
                raise ExportBlocked("EXPORT_INVALID_LINK")
            record["download_url"] = url
            record["phase"] = "LINK_READY"
            _save(journal, record)
            result.update(download_host=host, download_link_saved_locally=True)
        return result
    except ExportBlocked:
        raise
    except (DemoFailure, ValueError):
        raise ExportBlocked("EXPORT_STATUS_QUERY_FAILED_NO_RECREATE") from None
