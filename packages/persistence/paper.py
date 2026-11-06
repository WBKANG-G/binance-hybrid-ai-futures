"""PostgreSQL account serialization, fencing and atomic audit/outbox for PAPER only."""

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TypeVar
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, String, Table, Text, select, update
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.engine import Connection, Engine

from packages.persistence.market import metadata
from services.execution.models import Lease, Ledger, Venue
from services.risk.paper import RiskRejected

accounts = Table(
    "paper_accounts",
    metadata,
    Column("account_id", String(80), primary_key=True),
    Column("owner", String(36), nullable=True),
    Column("fence", BigInteger, nullable=False),
    Column("lease_until", DateTime(timezone=True), nullable=True),
    Column("state", JSONB, nullable=False),
)
venues = Table(
    "paper_venues",
    metadata,
    Column("account_id", ForeignKey("paper_accounts.account_id"), primary_key=True),
    Column("state", JSONB, nullable=False),
)
events = Table(
    "paper_events",
    metadata,
    Column("event_id", String(36), primary_key=True),
    Column("account_id", ForeignKey("paper_accounts.account_id"), nullable=False),
    Column("fence", BigInteger, nullable=False),
    Column("as_of", DateTime(timezone=True), nullable=False),
    Column("kind", String(80), nullable=False),
    Column("payload", Text, nullable=False),
)
outbox = Table(
    "paper_outbox",
    metadata,
    Column("event_id", ForeignKey("paper_events.event_id"), primary_key=True),
    Column("topic", String(80), nullable=False),
)
T = TypeVar("T")


class PaperStore:
    def __init__(self, engine: Engine):
        if engine.dialect.name != "postgresql":
            raise ValueError("PAPER requires transactional PostgreSQL")
        self.engine = engine

    def create(self, account_id: str, ledger: Ledger) -> None:
        if (
            not account_id.startswith("PAPER-")
            or len(account_id) > 80
            or ledger.policy.mode != "PAPER"
        ):
            raise ValueError("Dedicated PAPER account required")
        ledger = Ledger.model_validate(ledger.model_dump())
        with self.engine.begin() as conn:
            conn.execute(
                insert(accounts).values(
                    account_id=account_id, fence=0, state=ledger.model_dump(mode="json")
                )
            )
            conn.execute(
                insert(venues).values(
                    account_id=account_id, state=Venue(value=ledger.value).model_dump(mode="json")
                )
            )

    def acquire(self, account_id: str, owner: UUID, now: datetime, seconds: int = 30) -> Lease:
        if not 1 <= seconds <= 300:
            raise ValueError("Bounded lease required")
        with self.engine.begin() as conn:
            row = (
                conn.execute(
                    select(accounts).where(accounts.c.account_id == account_id).with_for_update()
                )
                .mappings()
                .one()
            )
            if row["lease_until"] is not None and row["lease_until"] > now:
                raise RiskRejected("Account leader lease already held")
            fence = int(row["fence"]) + 1
            ledger = Ledger.model_validate(row["state"])
            ledger.reconciled = False
            conn.execute(
                update(accounts)
                .where(accounts.c.account_id == account_id)
                .values(
                    owner=str(owner),
                    fence=fence,
                    lease_until=now + timedelta(seconds=seconds),
                    state=ledger.model_dump(mode="json"),
                )
            )
            self.audit(conn, account_id, fence, now, "LEADER_ACQUIRED", "{}")
            return Lease(account_id=account_id, owner=owner, fence=fence)

    def locked(self, conn: Connection, lease: Lease, now: datetime) -> Ledger:
        row = (
            conn.execute(
                select(accounts).where(accounts.c.account_id == lease.account_id).with_for_update()
            )
            .mappings()
            .one()
        )
        if (
            row["owner"] != str(lease.owner)
            or row["fence"] != lease.fence
            or row["lease_until"] <= now
        ):
            raise RiskRejected("Expired or superseded account fence")
        return Ledger.model_validate(row["state"])

    def audit(
        self, conn: Connection, account: str, fence: int, now: datetime, kind: str, payload: str
    ) -> None:
        event_id = str(uuid4())
        conn.execute(
            insert(events).values(
                event_id=event_id,
                account_id=account,
                fence=fence,
                as_of=now,
                kind=kind,
                payload=payload,
            )
        )
        conn.execute(insert(outbox).values(event_id=event_id, topic="PAPER_AUDIT"))

    def change(
        self,
        lease: Lease,
        now: datetime,
        kind: str,
        fn: Callable[[Ledger], T],
        *,
        require_current_venue: bool = False,
        replay_signal_id: str | None = None,
    ) -> T:
        with self.engine.begin() as conn:
            ledger = self.locked(conn, lease, now)
            original_policy = ledger.policy.model_dump_json()
            if require_current_venue and replay_signal_id not in ledger.orders:
                remote = conn.execute(
                    select(venues.c.state).where(venues.c.account_id == lease.account_id)
                ).scalar_one()
                if remote["revision"] != ledger.venue_revision:
                    raise RiskRejected("Venue changed; reconciliation required")
            result = fn(ledger)
            if ledger.policy.model_dump_json() != original_policy:
                raise RiskRejected("Risk policy is immutable; AI cannot change limits")
            conn.execute(
                update(accounts)
                .where(accounts.c.account_id == lease.account_id)
                .values(state=ledger.model_dump(mode="json"))
            )
            self.audit(conn, lease.account_id, lease.fence, now, kind, ledger.model_dump_json())
            return result

    def venue_change(self, lease: Lease, now: datetime, kind: str, fn: Callable[[Venue], T]) -> T:
        with self.engine.begin() as conn:
            self.locked(conn, lease, now)  # Fences simulator side effects, too.
            state = conn.execute(
                select(venues.c.state)
                .where(venues.c.account_id == lease.account_id)
                .with_for_update()
            ).scalar_one()
            venue = Venue.model_validate(state)
            original = venue.model_dump_json()
            result = fn(venue)
            if venue.model_dump_json() != original:
                venue.revision += 1
            conn.execute(
                update(venues)
                .where(venues.c.account_id == lease.account_id)
                .values(state=venue.model_dump(mode="json"))
            )
            self.audit(conn, lease.account_id, lease.fence, now, kind, venue.model_dump_json())
            return result

    def read(self, account_id: str) -> Ledger:
        with self.engine.connect() as conn:
            state = conn.execute(
                select(accounts.c.state).where(accounts.c.account_id == account_id)
            ).scalar_one()
            return Ledger.model_validate(state)
