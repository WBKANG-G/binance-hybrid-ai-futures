"""NAVER SMTP over verified implicit TLS; self-recipient, fixed safe templates."""

import smtplib
import ssl
import time
from collections.abc import Buffer, Callable
from datetime import UTC, datetime
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import format_datetime
from typing import Any
from uuid import UUID

from packages.persistence.demo_alerts import AlertEnvelope
from scripts.demo_alert_email_session import EmailSettings
from services.operations.demo_alert_delivery import DeliveryFailure

TITLES = {
    "STOP_PROTECTION_FAILURE": "손절 보호 확인 실패",
    "EXIT_OUTCOME_UNKNOWN": "청산 결과 확인 필요",
    "EMERGENCY_ENTERED": "비상 상태 전환",
    "RISK_LOCK_ENTERED": "위험 제한 상태 전환",
    "PRIVATE_SESSION_FAILED": "계좌 감시 세션 실패",
    "DELIVERY_PIPELINE_TEST": "알림 큐 자동 전달 테스트",
}


class DeadlineSMTP(smtplib.SMTP_SSL):
    def __init__(self, *args: Any, **kwargs: Any):
        self.deadline = time.monotonic() + 10
        super().__init__(*args, **kwargs)

    def _budget(self) -> None:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("SMTP_TIME_BUDGET_EXCEEDED")
        if self.sock is not None:
            self.sock.settimeout(min(5, remaining))

    def send(self, s: str | Buffer) -> None:
        self._budget()
        super().send(s)

    def getreply(self) -> tuple[int, bytes]:
        self._budget()
        return super().getreply()


class NaverEmail:
    def __init__(self, settings: EmailSettings, factory: Callable[..., Any] = DeadlineSMTP):
        self.settings, self.factory = settings, factory

    def _message(self, identifier: UUID, title: str, body: str) -> EmailMessage:
        message = EmailMessage(policy=SMTP)
        message["From"] = self.settings.address
        message["To"] = self.settings.address
        message["Subject"] = "[DEMO] " + title
        message["Date"] = format_datetime(datetime.now(UTC))
        message["Message-ID"] = f"<demo-alert-{identifier}@naver.com>"
        message.set_content(body)
        return message

    def _send(self, message: EmailMessage) -> None:
        try:
            with self.factory(
                "smtp.naver.com", 465, timeout=5, context=ssl.create_default_context()
            ) as client:
                client.login(
                    self.settings.address.split("@", 1)[0],
                    self.settings.password.get_secret_value(),
                )
                refused = client.send_message(
                    message, from_addr=self.settings.address, to_addrs=[self.settings.address]
                )
                if refused:
                    raise DeliveryFailure("SMTP_RECIPIENT_REJECTED")
        except smtplib.SMTPAuthenticationError:
            raise DeliveryFailure("SMTP_AUTH_FAILED") from None
        except smtplib.SMTPRecipientsRefused:
            raise DeliveryFailure("SMTP_RECIPIENT_REJECTED") from None
        except ssl.SSLCertVerificationError:
            raise DeliveryFailure("SMTP_TLS_FAILED") from None
        except (smtplib.SMTPServerDisconnected, TimeoutError, OSError):
            raise DeliveryFailure("SMTP_OUTCOME_UNKNOWN") from None
        except smtplib.SMTPException:
            raise DeliveryFailure("SMTP_REJECTED") from None

    def send(self, envelope: AlertEnvelope) -> None:
        if envelope.code == "DELIVERY_PIPELINE_TEST":
            body = (
                "DB 알림 큐와 자동 이메일 worker를 거친 전달 테스트입니다.\n"
                "실제 긴급 사건을 만들거나 주문·취소·청산을 실행하지 않았습니다.\n"
                f"유형: TEST\n계좌 참조: {envelope.account_ref}\n"
                f"알림 ID: {envelope.alert_id}\n"
            )
            self._send(self._message(envelope.alert_id, TITLES[envelope.code], body))
            return
        body = (
            "DEMO 계좌의 긴급 경고입니다. 실제 거래소 상태와 장부를 확인하세요.\n"
            "이 메일은 주문 재제출이나 신규 진입을 지시하지 않습니다.\n\n"
            f"코드: {envelope.code}\n계좌 참조: {envelope.account_ref}\n"
            f"발생 시각(UTC): {envelope.occurred_at.isoformat()}\n"
            f"위험 상태: {envelope.risk_state.value}\n진입 일시정지: {envelope.paused}\n"
            f"알림 ID: {envelope.alert_id}\n"
        )
        self._send(self._message(envelope.alert_id, TITLES[envelope.code], body))

    def send_test(self, identifier: UUID) -> None:
        self._send(
            self._message(
                identifier,
                "긴급 알림 수신 테스트",
                "네이버 본인 주소로 보내는 DEMO 알림 수신 테스트입니다.\n이 테스트는 실제 주문·취소·청산을 실행하지 않습니다.\n알림 ID: "
                + str(identifier),
            )
        )
