"""Default readonly DEMO alert counts; opt-in local receiver delivery only."""

import argparse
import json
import os
import time

from sqlalchemy import create_engine

from packages.domain.config import database_url
from scripts.demo_alert_email_session import status as email_status
from scripts.observe_demo_private import ACCOUNT
from services.operations.demo_alert_delivery import Dispatcher, LocalWebhook, summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", default=ACCOUNT)
    parser.add_argument("--deliver-local", action="store_true")
    parser.add_argument("--endpoint")
    parser.add_argument("--watch-seconds", type=int, default=0)
    args = parser.parse_args()
    if (
        bool(args.endpoint) != args.deliver_local
        or not 0 <= args.watch_seconds <= 300
        or (args.watch_seconds and not args.deliver_local)
        or not args.account.startswith("DEMO-")
    ):
        parser.error("Local delivery requires explicit endpoint; watch range 0..300s; DEMO only")
    engine = None
    success = False
    report: dict[str, object] = {
        "demo_alerts": "BLOCKED",
        "orders_enabled": False,
        "live_trading_enabled": False,
    }
    try:
        if (
            os.getenv("LIVE_TRADING_ENABLED", "false").lower() != "false"
            or os.getenv("TRADING_MODE", "PAPER").upper() == "LIVE"
        ):
            raise ValueError("DEMO context required")
        sink = LocalWebhook(args.endpoint) if args.deliver_local else None
        engine = create_engine(
            database_url().get_secret_value(), connect_args={"connect_timeout": 2}
        )
        deliveries: list[dict[str, str | bool | int]] = []
        if sink is not None:
            dispatcher = Dispatcher(engine)
            deadline = time.monotonic() + args.watch_seconds
            while True:
                deliveries.append(dispatcher.deliver_one(args.account, sink))
                if time.monotonic() >= deadline:
                    break
                time.sleep(min(1, max(0, deadline - time.monotonic())))
        report = {
            "demo_alerts": "LOCAL_DELIVERY_RUN" if sink else "READ_ONLY_QUEUE_SUMMARY",
            **summary(engine, args.account),
            "delivery_results": deliveries,
            "orders_enabled": False,
            "live_trading_enabled": False,
        }
        email_state = email_status()
        report.update(
            {
                "email_session": email_state,
                "external_receiver_configured": email_state == "CONFIGURED_PRIVATE_RAM",
                "email_ack_scope": "SMTP_ACCEPTANCE_NOT_INBOX_CONFIRMATION",
            }
        )
        success = True
    except Exception:
        report["failure"] = "ALERT_QUEUE_OR_LOCAL_DELIVERY_FAILED"
    finally:
        if engine is not None:
            engine.dispose()
    print(json.dumps(report), flush=True)
    if not success:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
