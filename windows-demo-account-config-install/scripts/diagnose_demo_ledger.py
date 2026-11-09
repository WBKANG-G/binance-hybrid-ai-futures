"""Read-only local DEMO ledger diagnostics. No exchange client or credentials."""

import json

from sqlalchemy import create_engine, select

from packages.domain.config import database_url
from packages.persistence.demo import accounts, events

ACCOUNT = "DEMO-WINDOWS-ISOLATED-BTC-V1"
OPERATION = "892f0bd6-66ba-4534-80b3-074039f676fb"
FIELDS = ("state", "position_state", "filled", "open_quantity", "protected", "exit_state")


def main() -> None:
    engine = create_engine(database_url().get_secret_value(), connect_args={"connect_timeout": 2})
    try:
        with engine.connect() as connection:
            # PostgreSQL enforces read-only for the entire transaction.
            from sqlalchemy import text

            connection.execute(text("SET TRANSACTION READ ONLY"))
            state = connection.execute(
                select(accounts.c.state).where(accounts.c.account_id == ACCOUNT)
            ).scalar_one()
            rows = connection.execute(
                select(events.c.as_of, events.c.kind, events.c.payload)
                .where(events.c.account_id == ACCOUNT)
                .order_by(events.c.as_of.desc())
                .limit(12)
            ).all()
            order = state.get("orders", {}).get(OPERATION, {})
            print(
                json.dumps(
                    {
                        "diagnostic": "LOCAL_DATABASE_READ_ONLY",
                        "risk_state": state.get("risk_state"),
                        "paused": state.get("paused"),
                        "failure_category": state.get(
                            "reconciliation_failure_code", "NOT_RECORDED_BY_OLD_VERSION"
                        ),
                        "order": {key: order.get(key) for key in FIELDS},
                        "live_trading_enabled": False,
                    }
                )
            )
            for stamp, kind, payload in reversed(rows):
                historical = json.loads(payload)
                print(
                    json.dumps(
                        {
                            "as_of": str(stamp),
                            "event": kind,
                            "failure_category": historical.get(
                                "reconciliation_failure_code", "NOT_RECORDED_BY_OLD_VERSION"
                            ),
                        }
                    )
                )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
