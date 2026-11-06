"""Isolated DEMO accounts, audit and outbox."""

from alembic import op
from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, MetaData, String, Table, Text
from sqlalchemy.dialects.postgresql import JSONB

revision = "0003_demo"
down_revision = "0002_paper"
branch_labels = None
depends_on = None
metadata = MetaData()
accounts = Table(
    "demo_accounts",
    metadata,
    Column("account_id", String(80), primary_key=True),
    Column("owner", String(36)),
    Column("fence", BigInteger, nullable=False),
    Column("lease_until", DateTime(timezone=True)),
    Column("state", JSONB, nullable=False),
)
events = Table(
    "demo_events",
    metadata,
    Column("event_id", String(36), primary_key=True),
    Column("account_id", ForeignKey("demo_accounts.account_id"), nullable=False),
    Column("fence", BigInteger, nullable=False),
    Column("as_of", DateTime(timezone=True), nullable=False),
    Column("kind", String(80), nullable=False),
    Column("payload", Text, nullable=False),
)
outbox = Table(
    "demo_outbox",
    metadata,
    Column("event_id", ForeignKey("demo_events.event_id"), primary_key=True),
    Column("topic", String(80), nullable=False),
)


def upgrade() -> None:
    metadata.create_all(op.get_bind(), checkfirst=False)


def downgrade() -> None:
    metadata.drop_all(op.get_bind(), checkfirst=False)
