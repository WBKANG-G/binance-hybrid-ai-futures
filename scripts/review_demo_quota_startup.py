"""Review local managed quota startup without credentials, HTTP, sessions, or writes."""

import argparse
import json
import os
from pathlib import Path

from sqlalchemy import create_engine

from packages.domain.config import database_url
from services.execution.demo_quota_startup_review import review_startup, stopped


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal-directory", type=Path, required=True)
    parser.add_argument("--account", default="DEMO-WINDOWS-ISOLATED-BTC-V1")
    args = parser.parse_args()
    engine = None
    result = stopped("LOCAL_REVIEW_FAILED")
    try:
        if (
            os.getenv("LIVE_TRADING_ENABLED", "false").lower() != "false"
            or os.getenv("TRADING_MODE", "PAPER").upper() == "LIVE"
        ):
            result = stopped("LIVE_CONTEXT_FORBIDDEN")
        elif not args.account.startswith("DEMO-") or len(args.account) > 80:
            result = stopped("DEMO_ACCOUNT_REQUIRED")
        else:
            engine = create_engine(
                database_url().get_secret_value(), connect_args={"connect_timeout": 2}
            )
            result = review_startup(engine, args.account, args.journal_directory)
    except Exception:
        result = stopped("LOCAL_REVIEW_FAILED")  # Never expose URLs, paths or raw errors.
    finally:
        if engine is not None:
            try:
                engine.dispose()
            except Exception:
                result = stopped("DATABASE_DISPOSE_FAILED")
    print(json.dumps(result), flush=True)
    if not result.get("local_checks_passed", False):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
