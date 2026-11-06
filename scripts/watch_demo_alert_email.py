"""Separate optional email worker: idle until a valid RAM self-email config exists."""

import json
import os
import time
from pathlib import Path

from sqlalchemy import create_engine

from packages.domain.config import database_url
from scripts.demo_alert_email_session import load
from scripts.observe_demo_private import ACCOUNT
from services.operations.demo_alert_delivery import Dispatcher
from services.operations.demo_alert_email import NaverEmail


def main() -> None:
    if (
        os.getenv("LIVE_TRADING_ENABLED", "false").lower() != "false"
        or os.getenv("TRADING_MODE", "PAPER").upper() == "LIVE"
    ):
        raise SystemExit("DEMO_ONLY_EMAIL_WORKER")
    engine = create_engine(
        database_url().get_secret_value(), connect_args={"connect_timeout": 2}, pool_pre_ping=True
    )
    dispatcher = Dispatcher(engine)
    last = ""
    auth_rejected_settings = None
    try:
        while True:
            Path("/tmp/demo-alert-watch-health").write_text(str(time.monotonic()))
            try:
                settings = load()
                report: dict[str, object] = {"email_worker": "WAITING_FOR_PRIVATE_RAM_CONFIG"}
                if settings is None:
                    auth_rejected_settings = None
                elif settings == auth_rejected_settings:
                    report = {
                        "email_worker": "WAITING_FOR_EMAIL_CONFIG_CHANGE",
                        "failure": "SMTP_AUTH_FAILED",
                    }
                else:
                    report = {
                        **dispatcher.deliver_one(ACCOUNT, NaverEmail(settings)),
                        "channel": "NAVER_SMTP",
                        "component": "DEMO_EMAIL_WORKER",
                    }
                    if report.get("result") == "SMTP_AUTH_FAILED":
                        auth_rejected_settings = settings
            except Exception:
                report = {
                    "email_worker": "BLOCKED_CONFIG_OR_DATABASE",
                    "external_details_logged": False,
                }
            encoded = json.dumps(report)
            if encoded != last:
                print(encoded, flush=True)
                last = encoded
            time.sleep(1)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
