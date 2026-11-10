"""Independent append-only PostgreSQL checkpoint for the shared SQLite window.

Lock order: PG scope advisory lock, then SQLite file lock. SQLite commits first;
PG commits second, before reserve can return a transport token. A commit gap is
blocked on the next access, never reconciled by refunding resource credit.
"""

import hashlib
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import (
    BigInteger,
    Column,
    ForeignKey,
    MetaData,
    String,
    Table,
    func,
    insert,
    select,
    text,
)
from sqlalchemy.engine import Connection, Engine

from packages.persistence.demo_order_quota import canonical
from packages.persistence.demo_shared_weight import POLICY, SharedWeightJournal, require

metadata = MetaData()
bindings = Table(
    "demo_shared_weight_bindings",
    metadata,
    Column("scope", String(80), primary_key=True),
    Column("journal", String(36), nullable=False, unique=True),
    Column("policy", String(64), nullable=False),
)
checkpoints = Table(
    "demo_shared_weight_checkpoints",
    metadata,
    Column("scope", ForeignKey(bindings.c.scope), primary_key=True),
    Column("revision", BigInteger, primary_key=True),
    Column("event_id", String(36), nullable=False, unique=True),
    Column("digest", String(64), nullable=False),
)


def fingerprint(connection: sqlite3.Connection) -> str:
    state = [
        [
            tuple(row)
            for row in connection.execute(
                "SELECT name,sql FROM sqlite_master WHERE type IN ('table','index','trigger') ORDER BY name"
            )
        ],
        [tuple(row) for row in connection.execute("SELECT * FROM meta")],
        [tuple(row) for row in connection.execute("SELECT * FROM attempts ORDER BY id")],
    ]
    return hashlib.sha256(canonical(state).encode()).hexdigest()


@dataclass(frozen=True)
class CheckpointContext:
    connection: Connection
    scope: str
    journal: UUID
    revision: int
    digest: str
    read_only: bool


class SharedWeightAnchorStore:
    def __init__(self, engine: Engine):
        require(engine.dialect.name == "postgresql", "SHARED_CHECKPOINT_POSTGRESQL_REQUIRED")
        self.engine = engine  # Caller owns this dedicated engine/pool.

    def provision(self) -> None:
        """Explicit auxiliary schema provisioning, never called by runtime/open."""
        with self.engine.begin() as c:
            bindings.create(c, checkfirst=False)
            checkpoints.create(c, checkfirst=False)
            c.execute(
                text("""CREATE FUNCTION demo_shared_weight_immutable() RETURNS trigger
                LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'shared_weight_append_only'; END $$""")
            )
            for table in (bindings, checkpoints):
                c.execute(
                    text(
                        f"CREATE TRIGGER shared_weight_immutable BEFORE UPDATE OR DELETE ON {table.name} FOR EACH ROW EXECUTE FUNCTION demo_shared_weight_immutable()"
                    )
                )

    @contextmanager
    def scope(
        self, scope: str, journal: UUID, *, read_only: bool, bootstrap: bool = False
    ) -> Iterator[CheckpointContext | Connection]:
        require(scope.startswith("DEMO-") and len(scope) <= 80, "SHARED_SCOPE_INVALID")
        key = int.from_bytes(
            hashlib.sha256(("demo-shared-weight:" + scope).encode()).digest()[:8],
            "big",
            signed=True,
        )
        with self.engine.connect() as c, c.begin():
            if read_only:
                c.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED, READ ONLY"))
            c.execute(text("SET LOCAL lock_timeout = '2s'"))
            c.execute(text("SET LOCAL statement_timeout = '2s'"))
            lock = "pg_advisory_xact_lock_shared" if read_only else "pg_advisory_xact_lock"
            c.execute(text(f"SELECT {lock}(:key)"), dict(key=key))
            row = (
                c.execute(select(bindings).where(bindings.c.scope == scope))
                .mappings()
                .one_or_none()
            )
            if bootstrap:
                require(not read_only and row is None, "SHARED_CHECKPOINT_ALREADY_BOUND")
                yield c
                return
            require(row is not None, "SHARED_CHECKPOINT_MISSING")
            assert row is not None
            require(
                (row["journal"], row["policy"]) == (str(journal), POLICY),
                "SHARED_CHECKPOINT_BINDING_MISMATCH",
            )
            latest = (
                c.execute(
                    select(checkpoints)
                    .where(checkpoints.c.scope == scope)
                    .order_by(checkpoints.c.revision.desc())
                    .limit(1)
                )
                .mappings()
                .one_or_none()
            )
            count = c.execute(
                select(func.count()).select_from(checkpoints).where(checkpoints.c.scope == scope)
            ).scalar_one()
            require(
                latest is not None and count == latest["revision"] + 1,
                "SHARED_CHECKPOINT_HISTORY_INVALID",
            )
            assert latest is not None
            require(
                latest["event_id"]
                == str(uuid5(journal, "shared-weight-checkpoint:" + str(latest["revision"]))),
                "SHARED_CHECKPOINT_HISTORY_INVALID",
            )
            yield CheckpointContext(
                c, scope, journal, latest["revision"], latest["digest"], read_only
            )

    def bootstrap(
        self, directory: Path, scope: str, journal: UUID, **kwargs: Any
    ) -> "AnchoredSharedWeightJournal":
        # Only new storage can be bound. An old unbound file is never adopted.
        with self.scope(scope, journal, read_only=False, bootstrap=True) as c:
            assert isinstance(c, Connection)
            SharedWeightJournal.create(directory, scope, journal, **kwargs)
            reader = SharedWeightJournal(directory, scope, journal, read_only=True, **kwargs)
            with reader._transaction() as (local, _, _):
                digest = fingerprint(local)
            c.execute(insert(bindings).values(scope=scope, journal=str(journal), policy=POLICY))
            self.append(c, scope, journal, 0, digest)
        return AnchoredSharedWeightJournal(directory, scope, journal, anchors=self, **kwargs)

    @staticmethod
    def append(
        connection: Connection, scope: str, journal: UUID, revision: int, digest: str
    ) -> None:
        connection.execute(
            insert(checkpoints).values(
                scope=scope,
                revision=revision,
                event_id=str(uuid5(journal, "shared-weight-checkpoint:" + str(revision))),
                digest=digest,
            )
        )


class AnchoredSharedWeightJournal(SharedWeightJournal):
    def __init__(self, *args: Any, anchors: SharedWeightAnchorStore, **kwargs: Any):
        self.anchors = anchors
        self.checkpoint: ContextVar[CheckpointContext | None] = ContextVar(
            "shared_checkpoint", default=None
        )
        super().__init__(*args, **kwargs)

    @classmethod
    def create(
        cls, directory: Path, scope: str, identity: UUID, **kwargs: Any
    ) -> "AnchoredSharedWeightJournal":
        raise NotImplementedError("Use explicit anchor-store bootstrap for new storage")

    @contextmanager
    def _transaction(self) -> Iterator[tuple[sqlite3.Connection, dict[str, Any], float]]:
        with self.anchors.scope(self.scope, self.identity, read_only=self.read_only) as state:
            assert isinstance(state, CheckpointContext)
            token = self.checkpoint.set(state)
            try:
                with super()._transaction() as local:
                    yield local
            finally:
                self.checkpoint.reset(token)

    def _verify_checkpoint(self, connection: sqlite3.Connection) -> None:
        state = self.checkpoint.get()
        require(
            state is not None and fingerprint(connection) == state.digest,
            "SHARED_CHECKPOINT_MISMATCH",
        )

    def _stage_checkpoint(self, connection: sqlite3.Connection) -> None:
        state = self.checkpoint.get()
        require(state is not None and not state.read_only, "SHARED_CHECKPOINT_WRITER_REQUIRED")
        assert state is not None
        digest = fingerprint(connection)
        if digest != state.digest:
            self.anchors.append(
                state.connection, state.scope, state.journal, state.revision + 1, digest
            )
