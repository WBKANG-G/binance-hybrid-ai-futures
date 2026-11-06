"""Dedicated synthetic tests only: separate DB, no key mounts, no real exchange."""

import json
import os
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, text

DATABASE = "postgresql+psycopg://fault:synthetic-local-only@127.0.0.1:5432/hybrid_fault_review"
SELECTED = [
    "tests/integration/test_demo_database_disconnect.py",
    "tests/integration/test_demo_protected_worker_sigkill.py",
    "tests/unit/test_demo_protected_worker_scope.py",
]


class Counts:
    def __init__(self) -> None:
        self.collected = self.passed = self.failed = self.skipped = 0

    def pytest_collection_finish(self, session: Any) -> None:
        self.collected = len(session.items)

    def pytest_runtest_logreport(self, report: Any) -> None:
        if report.when == "call" and report.passed:
            self.passed += 1
        if report.failed:
            self.failed += 1
        if report.skipped:
            self.skipped += 1


def check_scope() -> None:
    if (
        os.getenv("ISOLATED_PHASE4_FAULT_CHECK") != "1"
        or os.getenv("TEST_DATABASE_URL") != DATABASE
        or os.getenv("LIVE_TRADING_ENABLED", "false").lower() != "false"
        or os.getenv("TRADING_MODE", "PAPER").upper() != "PAPER"
        or "DATABASE_URL" in os.environ
        or any(name.startswith("BINANCE_") for name in os.environ)
        or Path("/run/demo-keys").exists()
    ):
        raise ValueError("ISOLATED_SYNTHETIC_CONTEXT_REQUIRED")


def main() -> None:
    try:
        check_scope()
        engine = create_engine(DATABASE, connect_args={"connect_timeout": 2})
        try:
            with engine.connect() as conn:
                if (
                    conn.execute(text("SELECT current_database()")).scalar_one()
                    != "hybrid_fault_review"
                ):
                    raise ValueError("ISOLATED_DATABASE_REQUIRED")
        finally:
            engine.dispose()
    except Exception:
        print(
            json.dumps(
                {
                    "fault_check": "BLOCKED_SCOPE_OR_TEST_DATABASE",
                    "details_logged": False,
                    "real_exchange_calls": 0,
                }
            ),
            flush=True,
        )
        raise SystemExit(2) from None
    counts = Counts()
    result = pytest.main(["-q", "-s", "-p", "no:cacheprovider", *SELECTED], plugins=[counts])
    accepted = (
        result == 0
        and counts.collected == counts.passed == 13
        and counts.failed == counts.skipped == 0
    )
    print(
        json.dumps(
            {
                "fault_check": "ISOLATED_SYNTHETIC_FAULT_CHECK_PASSED"
                if accepted
                else "NOT_ACCEPTED",
                "collected": counts.collected,
                "passed": counts.passed,
                "failed": counts.failed,
                "skipped": counts.skipped,
                "scope": "SYNTHETIC_EXCHANGE_AND_DEDICATED_DATABASE_ONLY",
                "real_exchange_calls": 0,
                "real_demo_account_verified": False,
                "orders_enabled": False,
                "live_trading_enabled": False,
            }
        ),
        flush=True,
    )
    raise SystemExit(0 if accepted else 2)


if __name__ == "__main__":
    main()
