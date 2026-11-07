"""Durable claimed alert delivery, outside trading transactions. Local sink only."""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import Engine, and_, insert, or_, select, text, update

from integrations.binance_usdm.rest import NoRedirect
from packages.persistence.demo_alerts import AlertEnvelope, alerts, attempts

CLAIM_SECONDS = 30
FAILURES = frozenset(
    {
        "HTTP_REJECTED",
        "TIMEOUT",
        "TRANSPORT_FAILED",
        "ACK_INVALID",
        "PAYLOAD_INVALID",
        "SINK_FAILED",
        "SMTP_AUTH_FAILED",
        "SMTP_RECIPIENT_REJECTED",
        "SMTP_TLS_FAILED",
        "SMTP_OUTCOME_UNKNOWN",
        "SMTP_REJECTED",
    }
)


class DeliveryFailure(ValueError):
    pass


class Sink(Protocol):
    def send(self, envelope: AlertEnvelope) -> None: ...


class LocalWebhook:
    """No production receiver is configured; refuses remote destinations and proxies."""

    def __init__(self, endpoint: str):
        url = urlsplit(endpoint)
        if (
            url.scheme != "http"
            or url.hostname != "127.0.0.1"
            or url.port is None
            or url.username
            or url.password
            or url.path != "/alerts"
            or url.query
            or url.fragment
        ):
            raise DeliveryFailure("LOCAL_RECEIVER_REQUIRED")
        self.endpoint = endpoint
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def send(self, envelope: AlertEnvelope) -> None:
        request = Request(
            self.endpoint,
            method="POST",
            data=envelope.model_dump_json().encode(),
            headers={"Content-Type": "application/json", "Idempotency-Key": str(envelope.alert_id)},
        )
        try:
            with self.opener.open(request, timeout=2) as response:
                if response.status not in {200, 202}:
                    raise DeliveryFailure("HTTP_REJECTED")
                body = response.read(4097)
        except HTTPError as error:
            error.close()
            raise DeliveryFailure("HTTP_REJECTED") from None
        except TimeoutError:
            raise DeliveryFailure("TIMEOUT") from None
        except URLError as error:
            raise DeliveryFailure(
                "TIMEOUT" if isinstance(error.reason, TimeoutError) else "TRANSPORT_FAILED"
            ) from None
        if len(body) > 4096:
            raise DeliveryFailure("ACK_INVALID")
        try:
            ack = json.loads(body)
        except (ValueError, UnicodeError):
            raise DeliveryFailure("ACK_INVALID") from None
        if (
            ack != {"accepted": True, "alert_id": str(envelope.alert_id)}
            or type(ack.get("accepted")) is not bool
        ):
            raise DeliveryFailure("ACK_INVALID")


@dataclass(frozen=True)
class Claim:
    alert_id: str
    event_id: str
    account: str
    code: str
    token: str
    number: int
    payload: dict[str, Any]


class Dispatcher:
    def __init__(self, engine: Engine, clock: Callable[[], datetime] | None = None):
        self.engine = engine
        self.now = clock or (lambda: datetime.now(UTC))

    def claim(self, account: str) -> Claim | None:
        now = self.now()
        with self.engine.begin() as conn:
            conn.execute(text("SET LOCAL statement_timeout='2s'"))
            row = (
                conn.execute(
                    select(alerts)
                    .where(
                        alerts.c.account_id == account,
                        alerts.c.delivered_at.is_(None),
                        alerts.c.next_attempt_at <= now,
                        or_(alerts.c.claim_token.is_(None), alerts.c.claim_until <= now),
                    )
                    .order_by(alerts.c.created_at, alerts.c.alert_id)
                    .with_for_update(skip_locked=True)
                    .limit(1)
                )
                .mappings()
                .first()
            )
            if row is None:
                return None
            if row["claim_token"] is not None:
                conn.execute(
                    update(attempts)
                    .where(
                        attempts.c.alert_id == row["alert_id"],
                        attempts.c.number == row["attempts"],
                        attempts.c.result == "SENDING",
                    )
                    .values(finished_at=now, result="CLAIM_EXPIRED")
                )
            token, number = str(uuid4()), int(row["attempts"]) + 1
            conn.execute(
                update(alerts)
                .where(alerts.c.alert_id == row["alert_id"])
                .values(
                    attempts=number,
                    claim_token=token,
                    claim_until=now + timedelta(seconds=CLAIM_SECONDS),
                )
            )
            conn.execute(
                insert(attempts).values(
                    attempt_id=str(uuid4()),
                    alert_id=row["alert_id"],
                    number=number,
                    started_at=now,
                    finished_at=None,
                    result="SENDING",
                )
            )
            return Claim(
                str(row["alert_id"]),
                str(row["event_id"]),
                str(row["account_id"]),
                str(row["code"]),
                token,
                number,
                row["payload"],
            )

    def finish(self, claim: Claim, result: str) -> bool:
        if result not in FAILURES | {"ACKNOWLEDGED"}:
            result = "SINK_FAILED"
        now = self.now()
        delay = min(300, 5 * 2 ** min(6, claim.number - 1))
        with self.engine.begin() as conn:
            conn.execute(text("SET LOCAL statement_timeout='2s'"))
            row = conn.execute(
                select(alerts.c.alert_id)
                .where(
                    alerts.c.alert_id == claim.alert_id,
                    alerts.c.claim_token == claim.token,
                    alerts.c.claim_until > now,
                    alerts.c.delivered_at.is_(None),
                )
                .with_for_update()
            ).first()
            if row is None:
                return False
            conn.execute(
                update(alerts)
                .where(alerts.c.alert_id == claim.alert_id)
                .values(
                    delivered_at=now if result == "ACKNOWLEDGED" else None,
                    next_attempt_at=now + timedelta(seconds=delay),
                    claim_token=None,
                    claim_until=None,
                    last_failure="NONE" if result == "ACKNOWLEDGED" else result,
                )
            )
            conn.execute(
                update(attempts)
                .where(
                    attempts.c.alert_id == claim.alert_id,
                    attempts.c.number == claim.number,
                    attempts.c.result == "SENDING",
                )
                .values(finished_at=now, result=result)
            )
            return True

    def deliver_one(self, account: str, sink: Sink) -> dict[str, str | bool | int]:
        claim = self.claim(account)
        if claim is None:
            return {"alert_delivery": "IDLE", "network_attempted": False}
        result = "ACKNOWLEDGED"
        sent = False
        try:
            envelope = AlertEnvelope.model_validate_json(json.dumps(claim.payload))
            if (
                str(envelope.alert_id) != claim.alert_id
                or str(envelope.event_id) != claim.event_id
                or envelope.code != claim.code
                or envelope.account_ref != hashlib.sha256(claim.account.encode()).hexdigest()[:24]
            ):
                raise ValueError("payload identity mismatch")
        except (ValidationError, ValueError, TypeError):
            result = "PAYLOAD_INVALID"
        else:
            try:
                sent = True
                sink.send(envelope)
            except DeliveryFailure as error:
                result = str(error) if str(error) in FAILURES else "SINK_FAILED"
            except Exception:
                result = "SINK_FAILED"
        committed = self.finish(claim, result)
        return {
            "alert_delivery": "DELIVERED"
            if committed and result == "ACKNOWLEDGED"
            else "RETRY_PENDING"
            if committed
            else "CLAIM_LOST",
            "result": result if committed else "CLAIM_LOST",
            "attempt": claim.number,
            "alert_id": claim.alert_id,
            "network_attempted": sent,
        }


def summary(engine: Engine, account: str) -> dict[str, int | bool]:
    from sqlalchemy import func

    with engine.connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        conn.execute(text("SET LOCAL statement_timeout='2s'"))
        row = (
            conn.execute(
                select(
                    func.count().label("total"),
                    func.count().filter(alerts.c.delivered_at.is_(None)).label("pending"),
                    func.count().filter(alerts.c.delivered_at.is_not(None)).label("delivered"),
                    func.count()
                    .filter(and_(alerts.c.delivered_at.is_(None), alerts.c.attempts > 0))
                    .label("retry_pending"),
                ).where(alerts.c.account_id == account)
            )
            .mappings()
            .one()
        )
    return {
        "total": int(row["total"]),
        "pending": int(row["pending"]),
        "delivered": int(row["delivered"]),
        "retry_pending": int(row["retry_pending"]),
        "external_receiver_configured": False,
    }
