"""Bounded local database probe. Does not create application tables."""

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from packages.domain.config import database_url

EXPECTED_SCHEMA_REVISION = "0004_demo_urgent_alerts"


def probe_database() -> bool:
    engine: Engine | None = None
    try:
        engine = create_engine(
            database_url().get_secret_value(), connect_args={"connect_timeout": 2}
        )
        with engine.connect() as connection:
            return bool(
                connection.execute(text("SELECT 1")).scalar_one() == 1
                and connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == EXPECTED_SCHEMA_REVISION
            )
    except (SQLAlchemyError, ValueError):
        # Deliberately do not log driver errors, which can include connection data.
        return False
    finally:
        if engine is not None:
            engine.dispose()
