"""Dedicated DEMO storage and fenced, serialized network effects.

An intent commits before an effect transaction. The latter holds the account row
lock across a bounded DEMO HTTP call: a successor cannot race an in-flight writer.
If DB commit fails after a POST, the persisted SUBMITTING intent is reconciled.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TypeVar
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    String,
    Table,
    Text,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.engine import Engine

from packages.persistence.demo_alerts import enqueue as enqueue_alerts
from packages.persistence.demo_alerts import signals as alert_signals
from packages.persistence.market import metadata
from services.execution.demo_models import DemoLedger
from services.execution.models import Lease
from services.risk.paper import RiskRejected

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
T = TypeVar("T")


class DemoStore:
    def __init__(self, engine: Engine, clock: Callable[[], datetime] | None = None):
        if engine.dialect.name != "postgresql":
            raise ValueError("DEMO requires PostgreSQL")
        self.engine = engine
        self.now = clock or (lambda: datetime.now(UTC))

    def create(self, account_id: str, ledger: DemoLedger) -> None:
        if not account_id.startswith("DEMO-") or len(account_id) > 80:
            raise ValueError("Dedicated DEMO account namespace required")
        ledger = DemoLedger.model_validate(ledger.model_dump())
        with self.engine.begin() as conn:
            conn.execute(
                insert(accounts).values(
                    account_id=account_id, fence=0, state=ledger.model_dump(mode="json")
                )
            )

    def acquire(self, account_id: str, owner: UUID) -> Lease:
        with self.engine.begin() as conn:
            conn.execute(text("SET LOCAL lock_timeout = '2s'"))
            row = (
                conn.execute(
                    select(accounts).where(accounts.c.account_id == account_id).with_for_update()
                )
                .mappings()
                .one()
            )
            now = self.now()
            if row["lease_until"] and row["lease_until"] > now:
                raise RiskRejected("DEMO account leader already active")
            ledger = DemoLedger.model_validate(row["state"])
            ledger.reconciled = False
            ledger.stream_ready = False
            fence = int(row["fence"]) + 1
            conn.execute(
                update(accounts)
                .where(accounts.c.account_id == account_id)
                .values(
                    owner=str(owner),
                    fence=fence,
                    lease_until=now + timedelta(seconds=60),
                    state=ledger.model_dump(mode="json"),
                )
            )
            event_id = str(uuid4())
            conn.execute(
                insert(events).values(
                    event_id=event_id,
                    account_id=account_id,
                    fence=fence,
                    as_of=now,
                    kind="DEMO_LEADER_ACQUIRED",
                    payload=ledger.model_dump_json(),
                )
            )
            conn.execute(insert(outbox).values(event_id=event_id, topic="DEMO_AUDIT"))
            return Lease(account_id=account_id, owner=owner, fence=fence)

    def change(self, lease: Lease, kind: str, apply: Callable[[DemoLedger], T]) -> T:
        with self.engine.begin() as conn:
            conn.execute(text("SET LOCAL lock_timeout = '2s'"))
            row = (
                conn.execute(
                    select(accounts)
                    .where(accounts.c.account_id == lease.account_id)
                    .with_for_update()
                )
                .mappings()
                .one()
            )
            now = self.now()
            if (
                row["owner"] != str(lease.owner)
                or row["fence"] != lease.fence
                or row["lease_until"] <= now
            ):
                raise RiskRejected("DEMO lease expired or fenced")
            ledger = DemoLedger.model_validate(row["state"])
            policy = ledger.policy.model_dump_json()
            alert_before = alert_signals(ledger)
            result = apply(ledger)
            if ledger.policy.model_dump_json() != policy:
                raise RiskRejected("DEMO risk policy cannot be mutated")
            if self.now() >= row["lease_until"]:
                raise RiskRejected("DEMO operation outlived lease; query unresolved intent")
            conn.execute(
                update(accounts)
                .where(accounts.c.account_id == lease.account_id)
                .values(state=ledger.model_dump(mode="json"))
            )
            event_id = str(uuid4())
            conn.execute(
                insert(events).values(
                    event_id=event_id,
                    account_id=lease.account_id,
                    fence=lease.fence,
                    as_of=now,
                    kind=kind,
                    payload=ledger.model_dump_json(),
                )
            )
            conn.execute(insert(outbox).values(event_id=event_id, topic="DEMO_AUDIT"))
            enqueue_alerts(conn, event_id, lease.account_id, now, kind, alert_before, ledger)
            return result

    def renew(self, lease: Lease) -> None:
        # Reuse fenced change, then extend under a second short row-locked transaction.
        def heartbeat(ledger: DemoLedger) -> None:
            pass

        self.change(lease, "HEARTBEAT", heartbeat)
        with self.engine.begin() as conn:
            row = (
                conn.execute(
                    select(accounts)
                    .where(accounts.c.account_id == lease.account_id)
                    .with_for_update()
                )
                .mappings()
                .one()
            )
            if (
                row["owner"] != str(lease.owner)
                or row["fence"] != lease.fence
                or row["lease_until"] <= self.now()
            ):
                raise RiskRejected("Cannot renew expired DEMO lease")
            conn.execute(
                update(accounts)
                .where(accounts.c.account_id == lease.account_id)
                .values(lease_until=self.now() + timedelta(seconds=60))
            )

    def read(self, account_id: str) -> DemoLedger:
        with self.engine.connect() as conn:
            raw = conn.execute(
                select(accounts.c.state).where(accounts.c.account_id == account_id)
            ).scalar_one()
            return DemoLedger.model_validate(raw)

    def release(self, lease: Lease) -> None:
        with self.engine.begin() as conn:
            row = (
                conn.execute(
                    select(accounts)
                    .where(accounts.c.account_id == lease.account_id)
                    .with_for_update()
                )
                .mappings()
                .one()
            )
            if row["owner"] != str(lease.owner) or row["fence"] != lease.fence:
                raise RiskRejected("Old DEMO leader cannot release successor lease")
            ledger = DemoLedger.model_validate(row["state"])
            ledger.reconciled = ledger.stream_ready = False
            conn.execute(
                update(accounts)
                .where(accounts.c.account_id == lease.account_id)
                .values(owner=None, lease_until=self.now(), state=ledger.model_dump(mode="json"))
            )
            event_id = str(uuid4())
            conn.execute(
                insert(events).values(
                    event_id=event_id,
                    account_id=lease.account_id,
                    fence=lease.fence,
                    as_of=self.now(),
                    kind="DEMO_LEADER_RELEASED",
                    payload=ledger.model_dump_json(),
                )
            )
            conn.execute(insert(outbox).values(event_id=event_id, topic="DEMO_AUDIT"))
