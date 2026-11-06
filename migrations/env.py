"""Migration configuration never logs database URL or environment values."""

from alembic import context
from sqlalchemy import create_engine

from packages.domain.config import database_url
from packages.persistence import (
    demo,  # noqa: F401 — register isolated DEMO tables
    paper,  # noqa: F401 — register additive PAPER tables
)
from packages.persistence.market import metadata


def run() -> None:
    config = context.config
    if context.is_offline_mode():
        context.configure(
            url=database_url().get_secret_value(), target_metadata=metadata, literal_binds=True
        )
        with context.begin_transaction():
            context.run_migrations()
        return
    provided = config.attributes.get("connection")
    if provided is not None:
        context.configure(connection=provided, target_metadata=metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = create_engine(database_url().get_secret_value())
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=metadata, compare_type=True)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


run()
