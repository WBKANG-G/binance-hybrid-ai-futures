"""Queue exactly one TEST alert for the existing background worker; never sends directly."""

import argparse
import json
import os
import time
from uuid import UUID

from sqlalchemy import create_engine

from packages.domain.config import database_url
from scripts.demo_alert_email_session import status as email_status
from scripts.observe_demo_private import ACCOUNT
from services.operations.demo_alert_pipeline import enqueue_test, review_test


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--enqueue-self-test", action="store_true")
    action.add_argument("--review", action="store_true")
    parser.add_argument("--operation-id", type=UUID, required=True)
    parser.add_argument("--wait-seconds", type=int, default=30)
    args = parser.parse_args()
    if args.operation_id.version != 4 or not 0 <= args.wait_seconds <= 60:
        parser.error("UUID4 and bounded wait 0..60s required")
    engine = None
    report: dict[str, object] = {
        "pipeline_check": "BLOCKED",
        "orders_enabled": False,
        "live_trading_enabled": False,
    }
    success = False
    try:
        if (
            os.getenv("LIVE_TRADING_ENABLED", "false").lower() != "false"
            or os.getenv("TRADING_MODE", "PAPER").upper() == "LIVE"
        ):
            raise ValueError("DEMO_ONLY_CONTEXT_REQUIRED")
        if args.enqueue_self_test and email_status() != "CONFIGURED_PRIVATE_RAM":
            raise ValueError("VALID_EMAIL_RAM_SESSION_REQUIRED")
        engine = create_engine(
            database_url().get_secret_value(), connect_args={"connect_timeout": 2}
        )
        created = (
            enqueue_test(engine, ACCOUNT, args.operation_id) if args.enqueue_self_test else False
        )
        deadline = time.monotonic() + (args.wait_seconds if args.enqueue_self_test else 0)
        while True:
            report = {
                **review_test(engine, ACCOUNT, args.operation_id),
                "newly_registered": created,
            }
            if (
                report["pipeline_check"] not in {"QUEUED_WAITING_FOR_WORKER", "WORKER_PROCESSING"}
                or time.monotonic() >= deadline
            ):
                break
            time.sleep(min(0.5, max(0, deadline - time.monotonic())))
        success = report["pipeline_check"] == "QUEUE_DELIVERY_ACKNOWLEDGED"
    except Exception:
        report = {
            "pipeline_check": "BLOCKED",
            "failure": "QUEUE_TEST_CONTEXT_OR_DATABASE_FAILED",
            "operation_id": str(args.operation_id),
            "orders_enabled": False,
            "live_trading_enabled": False,
        }
    finally:
        if engine is not None:
            engine.dispose()
    print(json.dumps(report), flush=True)
    if not success:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
