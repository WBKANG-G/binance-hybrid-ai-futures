import os
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text


@pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"), reason="Set TEST_DATABASE_URL for PostgreSQL integration"
)
def test_postgres_decimal_transaction() -> None:
    engine = create_engine(os.environ["TEST_DATABASE_URL"])
    try:
        with engine.begin() as connection:
            connection.execute(
                text("CREATE TEMP TABLE phase0_amounts (amount NUMERIC NOT NULL) ON COMMIT DROP")
            )
            connection.execute(
                text("INSERT INTO phase0_amounts VALUES (:amount)"),
                {"amount": Decimal("0.123456789012345678")},
            )
            assert connection.execute(
                text("SELECT amount FROM phase0_amounts")
            ).scalar_one() == Decimal("0.123456789012345678")
    finally:
        engine.dispose()
