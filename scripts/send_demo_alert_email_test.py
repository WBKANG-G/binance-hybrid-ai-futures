"""Explicit single self-email test; no DB, no exchange and no credential output."""

import argparse
import json
from uuid import UUID

from scripts.demo_alert_email_session import load
from services.operations.demo_alert_delivery import DeliveryFailure
from services.operations.demo_alert_email import NaverEmail


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--send-self-test", action="store_true", required=True)
    parser.add_argument("--operation-id", type=UUID, required=True)
    args = parser.parse_args()
    try:
        settings = load()
        if settings is None:
            raise DeliveryFailure("EMAIL_NOT_CONFIGURED")
        NaverEmail(settings).send_test(args.operation_id)
        print(
            json.dumps(
                {
                    "email_test": "SMTP_ACCEPTED_CHECK_YOUR_INBOX",
                    "operation_id": str(args.operation_id),
                    "orders_enabled": False,
                    "live_trading_enabled": False,
                }
            ),
            flush=True,
        )
    except Exception as error:
        safe = (
            str(error)
            if isinstance(error, DeliveryFailure)
            and str(error)
            in {
                "SMTP_AUTH_FAILED",
                "SMTP_RECIPIENT_REJECTED",
                "SMTP_TLS_FAILED",
                "SMTP_OUTCOME_UNKNOWN",
                "SMTP_REJECTED",
                "EMAIL_NOT_CONFIGURED",
            }
            else "EMAIL_TEST_FAILED"
        )
        print(
            json.dumps(
                {
                    "email_test": "NOT_CONFIRMED_DO_NOT_REPEAT",
                    "failure": safe,
                    "orders_enabled": False,
                    "live_trading_enabled": False,
                }
            ),
            flush=True,
        )
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
