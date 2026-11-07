"""Public market data schema snapshot. Do not import mutable application metadata."""

from alembic import op
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
)

revision = "0001_market_data"
down_revision = None
branch_labels = None
depends_on = None

metadata = MetaData()
raw_events = Table(
    "market_raw_events",
    metadata,
    Column("source", String(40), primary_key=True),
    Column("digest", String(64), primary_key=True),
    Column("symbol", String(30), nullable=False),
    Column("event_type", String(40), nullable=False),
    Column("payload", Text, nullable=False),
    Column("received_at", DateTime(timezone=True), nullable=False),
)
candles = Table(
    "market_candles",
    metadata,
    Column("source", String(40), primary_key=True),
    Column("symbol", String(30), primary_key=True),
    Column("interval", String(10), primary_key=True),
    Column("open_time", DateTime(timezone=True), primary_key=True),
    Column("close_time", DateTime(timezone=True), nullable=False),
    Column("open", Numeric, nullable=False),
    Column("high", Numeric, nullable=False),
    Column("low", Numeric, nullable=False),
    Column("close", Numeric, nullable=False),
    Column("volume", Numeric, nullable=False),
    Column("quote_volume", Numeric, nullable=False),
    Column("as_of", DateTime(timezone=True), nullable=False),
    Column("received_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "low <= open AND low <= close AND high >= open AND high >= close AND low > 0",
        name="ck_market_candle_ohlc",
    ),
    CheckConstraint("volume >= 0 AND quote_volume >= 0", name="ck_market_candle_volume"),
)
books = Table(
    "market_books",
    metadata,
    Column("source", String(40), primary_key=True),
    Column("symbol", String(30), primary_key=True),
    Column("update_id", BigInteger, primary_key=True),
    Column("bid", Numeric, nullable=False),
    Column("ask", Numeric, nullable=False),
    Column("bid_quantity", Numeric, nullable=False),
    Column("ask_quantity", Numeric, nullable=False),
    Column("as_of", DateTime(timezone=True), nullable=False),
    Column("received_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "bid > 0 AND ask > bid AND bid_quantity > 0 AND ask_quantity > 0",
        name="ck_market_book_spread",
    ),
)
stream_status = Table(
    "market_stream_status",
    metadata,
    Column("source", String(40), primary_key=True),
    Column("symbol", String(30), primary_key=True),
    Column("interval", String(10), primary_key=True),
    Column("checked_at", DateTime(timezone=True), nullable=False),
    Column("book_as_of", DateTime(timezone=True)),
    Column("candle_as_of", DateTime(timezone=True)),
    Column("book_received", DateTime(timezone=True)),
    Column("candle_received", DateTime(timezone=True)),
    Column("reconciliation_required", Boolean, nullable=False),
    Column("reason", String(40), nullable=False),
    Column("book_age_ms", Integer, nullable=False),
    Column("candle_age_ms", Integer, nullable=False),
    CheckConstraint("book_age_ms > 0 AND candle_age_ms > 0", name="ck_market_freshness_budget"),
)


def upgrade() -> None:
    metadata.create_all(bind=op.get_bind(), checkfirst=False)


def downgrade() -> None:
    metadata.drop_all(bind=op.get_bind(), checkfirst=False)
