"""Append-only PG evidence for GET-only activity in an existing shared journal.

Provisioning and binding are explicit. Checkpoint and audit append use the same
PG transaction; SQLite commits first. Any cross-store commit gap stays blocked.
"""

import copy
import hashlib
import math
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine

from integrations.binance_usdm.demo_tp_weight_model import MODELED_TP_ROUTE_COSTS
from packages.persistence.demo_order_quota import canonical, quota_require
from packages.persistence.demo_shared_checkpoint import (
    INSTALLATION_KEY,
    AnchoredSharedWeightJournal,
    CheckpointContext,
    checkpoints,
    installations,
)
from packages.persistence.demo_shared_checkpoint import (
    bindings as shared_bindings,
)
from packages.persistence.demo_shared_weight import WINDOW, SharedWeightJournal
from services.execution.demo_runtime_cost_contract import require_runtime_cost_contract

POLICY_VERSION = "ANCHORED_DEMO_READ_AUDIT_V1"
MAX_RECORDS = 1024
MAX_ROWS = 256
MAX_STATE_BYTES = 65_536
metadata = MetaData()
read_bindings = Table(
    "demo_read_audit_bindings",
    metadata,
    Column("scope", ForeignKey(shared_bindings.c.scope), primary_key=True),
    Column("journal", String(36), nullable=False, unique=True),
    Column("installation", String(36), nullable=False),
    Column("policy", String(64), nullable=False),
    Column("base_revision", BigInteger, nullable=False),
    Column("base_digest", String(64), nullable=False),
    Column("base_state", JSONB, nullable=False),
    Column("root_hash", String(64), nullable=False),
)
read_events = Table(
    "demo_read_audit_events",
    metadata,
    Column("scope", ForeignKey(read_bindings.c.scope), primary_key=True),
    Column("sequence", BigInteger, primary_key=True),
    Column("event_id", String(36), nullable=False, unique=True),
    Column("revision", BigInteger, nullable=False),
    Column("checkpoint_digest", String(64), nullable=False),
    Column("token", String(36), index=True),
    Column("action", JSONB, nullable=False),
    Column("state", JSONB, nullable=False),
    Column("previous_hash", String(64), nullable=False),
    Column("hash", String(64), nullable=False),
)


def require(ok: bool, code: str) -> None:
    quota_require(ok, code)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def snapshot(c: sqlite3.Connection) -> list[Any]:
    value = [
        [
            list(row)
            for row in c.execute(
                "SELECT name,sql FROM sqlite_master WHERE type IN ('table','index','trigger') ORDER BY name"
            )
        ],
        [list(row) for row in c.execute("SELECT * FROM meta")],
        [list(row) for row in c.execute("SELECT * FROM attempts ORDER BY id")],
    ]
    require(
        len(value[1]) == 1
        and len(value[2]) <= MAX_ROWS
        and len(canonical(value).encode()) <= MAX_STATE_BYTES,
        "READ_AUDIT_STATE_LIMIT",
    )
    return value


def _root_body(row: Any) -> dict[str, Any]:
    return {
        name: row[name]
        for name in (
            "scope",
            "journal",
            "installation",
            "policy",
            "base_revision",
            "base_digest",
            "base_state",
        )
    }


def _event_body(row: Any) -> dict[str, Any]:
    return {
        name: row[name]
        for name in (
            "scope",
            "sequence",
            "event_id",
            "revision",
            "checkpoint_digest",
            "token",
            "action",
            "state",
            "previous_hash",
        )
    }


def _rows(state: Any) -> dict[str, list[Any]]:
    require(
        isinstance(state, list)
        and len(state) == 3
        and len(state[1]) == 1
        and len(state[1][0]) == 9
        and len(state[2]) <= MAX_ROWS
        and len(canonical(state).encode()) <= MAX_STATE_BYTES,
        "READ_AUDIT_STATE_INVALID",
    )
    result = {}
    for row in state[2]:
        require(
            isinstance(row, list)
            and len(row) == 7
            and isinstance(row[0], str)
            and str(UUID(row[0])) == row[0]
            and row[0] not in result,
            "READ_AUDIT_STATE_INVALID",
        )
        result[row[0]] = row
    return result


def validate_transition(before: Any, after: Any, action: dict[str, Any]) -> None:
    if action.get("kind") == "CLOCK_REBASE":
        from packages.persistence.demo_read_recovery import validate_rebase_transition

        validate_rebase_transition(before, after, action)
        return
    old, new = _rows(before), _rows(after)
    b, a = before[1][0], after[1][0]
    require(
        before[0] == after[0]
        and a[:6] == b[:6]
        and all(type(x) in (int, float) and math.isfinite(x) for x in (b[6], a[6]))
        and a[6] >= b[6]
        and type(a[7]) is int
        and type(a[8]) is int
        and a[8] in (0, 1)
        and b[8] == 0,
        "READ_AUDIT_TRANSITION_INVALID",
    )
    require(
        set(action) == {"kind", "path", "cost", "token", "status", "weight", "sent", "observe"},
        "READ_AUDIT_ACTION_INVALID",
    )
    removed = old.keys() - new.keys()
    require(
        all(old[k][4] is not None and old[k][4] <= a[6] - WINDOW for k in removed),
        "READ_AUDIT_PENDING_REMOVED",
    )
    added = new.keys() - old.keys()
    changed = {k for k in old.keys() & new.keys() if old[k] != new[k]}
    kind, token = action["kind"], action["token"]
    if kind == "RESERVE":
        cost = MODELED_TP_ROUTE_COSTS.get(("GET", action["path"]))
        require(
            type(cost) is int
            and cost > 0
            and type(action["cost"]) is int
            and action["cost"] == cost
            and token in added
            and added == {token}
            and not changed
            and a[7] == b[7] + cost
            and a[8] == 0
            and action["status"] is action["weight"] is None
            and action["sent"] is False
            and action["observe"] is True,
            "READ_AUDIT_RESERVATION_INVALID",
        )
        pending = sum(row[1] for row in old.values() if row[4] is None)
        require(
            new[token] == [token, cost, a[7], pending, None, None, None],
            "READ_AUDIT_RESERVATION_INVALID",
        )
    elif kind == "FINISH":
        require(
            token in old
            and token in new
            and old[token][4] is None
            and not added
            and a[7] == b[7]
            and type(action["sent"]) is bool
            and type(action["observe"]) is bool,
            "READ_AUDIT_COMPLETION_INVALID",
        )
        require(
            type(action["cost"]) is int
            and MODELED_TP_ROUTE_COSTS.get(("GET", action["path"]))
            == action["cost"]
            == old[token][1],
            "READ_AUDIT_ACTION_INVALID",
        )
        require(
            (
                not action["sent"]
                and action["observe"] is True
                and action["status"] is None
                and action["weight"] is None
            )
            or (
                action["sent"]
                and action["observe"] == (action["path"] != "/fapi/v1/ticker/bookTicker")
                and (action["observe"] or action["weight"] is None)
            ),
            "READ_AUDIT_ACTION_INVALID",
        )
        status, weight = action["status"], action["weight"]
        require(
            (status is None or type(status) is int and 100 <= status <= 599)
            and (weight is None or type(weight) is int and 0 <= weight < 10**12),
            "READ_AUDIT_ACTION_INVALID",
        )
        invalid = action["sent"] and (
            (action["observe"] and weight is None) or status is None or status in (418, 429)
        )
        if invalid:
            require(a[8] == 1 and not changed, "READ_AUDIT_COMPLETION_INVALID")
        else:
            floor = None
            if action["sent"] and action["observe"]:
                require(weight is not None, "READ_AUDIT_COMPLETION_INVALID")
                floor = weight + a[7] - old[token][2] + old[token][3]
            require(
                a[8] == 0
                and changed == {token}
                and new[token] == old[token][:4] + [a[6], floor, a[7]],
                "READ_AUDIT_COMPLETION_INVALID",
            )
    elif kind == "STOP":
        require(
            a[7] == b[7]
            and a[8] == 1
            and not added
            and not changed
            and any(row[4] is None for row in old.values())
            and all(action[k] is None for k in ("path", "cost", "token", "status", "weight"))
            and action["sent"] is True
            and action["observe"] is True,
            "READ_AUDIT_STOP_INVALID",
        )
    else:
        require(False, "READ_AUDIT_ACTION_INVALID")


class ReadAuditStore:
    def __init__(self, engine: Engine):
        require(engine.dialect.name == "postgresql", "READ_AUDIT_POSTGRESQL_REQUIRED")
        self.engine = engine

    def provision(self, *, apply: bool = False) -> None:
        require(apply is True, "READ_AUDIT_EXPLICIT_PROVISION_REQUIRED")
        with self.engine.begin() as c:
            read_bindings.create(c, checkfirst=False)
            read_events.create(c, checkfirst=False)
            c.execute(
                text(
                    "CREATE FUNCTION demo_read_audit_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'demo_read_audit_append_only'; END $$"
                )
            )
            for table in (read_bindings, read_events):
                c.execute(
                    text(
                        f"CREATE TRIGGER demo_read_audit_immutable BEFORE UPDATE OR DELETE ON {table.name} FOR EACH ROW EXECUTE FUNCTION demo_read_audit_immutable()"
                    )
                )

    def bind_empty(self, shared: AnchoredSharedWeightJournal, *, apply: bool = False) -> None:
        require(apply is True, "READ_AUDIT_EXPLICIT_BIND_REQUIRED")
        require(shared.anchors.engine is self.engine, "READ_AUDIT_ANCHOR_ENGINE_REQUIRED")
        reader = copy.copy(shared)
        reader.read_only = True
        with shared.anchors.scope(shared.scope, shared.identity, read_only=False) as cp:
            assert isinstance(cp, CheckpointContext)
            marker = reader.checkpoint.set(cp)
            try:
                with SharedWeightJournal._transaction(reader) as (c, meta, _):
                    existing = (
                        cp.connection.execute(
                            select(read_bindings).where(read_bindings.c.scope == shared.scope)
                        )
                        .mappings()
                        .one_or_none()
                    )
                    if existing is not None:
                        self.verify_tip(cp, c)
                        return
                    state = snapshot(c)
                    require(
                        meta["total"] == 0 and not state[2], "READ_AUDIT_USED_STORE_NOT_ADOPTED"
                    )
                    row = dict(
                        scope=shared.scope,
                        journal=str(shared.identity),
                        installation=str(shared.anchors._installation_on(cp.connection)),
                        policy=POLICY_VERSION,
                        base_revision=cp.revision,
                        base_digest=cp.digest,
                        base_state=state,
                    )
                    row["root_hash"] = digest(row)
                    cp.connection.execute(insert(read_bindings).values(**row))
            finally:
                reader.checkpoint.reset(marker)

    def _binding(self, cp: CheckpointContext) -> Any:
        row = (
            cp.connection.execute(select(read_bindings).where(read_bindings.c.scope == cp.scope))
            .mappings()
            .one_or_none()
        )
        require(row is not None, "READ_AUDIT_BINDING_MISSING")
        assert row is not None
        installation = cp.connection.execute(
            select(installations.c.identity).where(installations.c.name == INSTALLATION_KEY)
        ).scalar_one()
        require(row["installation"] == installation, "READ_AUDIT_INSTALLATION_CHANGED")
        require(
            row["journal"] == str(cp.journal)
            and row["policy"] == POLICY_VERSION
            and digest(_root_body(row)) == row["root_hash"]
            and digest(row["base_state"]) == row["base_digest"]
            and row["base_state"][1][0][7:] == [0, 0]
            and not row["base_state"][2],
            "READ_AUDIT_BINDING_INVALID",
        )
        witness = cp.connection.execute(
            select(checkpoints.c.digest).where(
                checkpoints.c.scope == cp.scope, checkpoints.c.revision == row["base_revision"]
            )
        ).scalar_one()
        require(witness == row["base_digest"], "READ_AUDIT_CHECKPOINT_MISMATCH")
        return row

    def verify_tip(self, cp: CheckpointContext, c: sqlite3.Connection) -> tuple[Any, Any, int]:
        binding = self._binding(cp)
        count = cp.connection.execute(
            select(func.count()).select_from(read_events).where(read_events.c.scope == cp.scope)
        ).scalar_one()
        require(0 <= count <= MAX_RECORDS, "READ_AUDIT_RECORD_LIMIT")
        tip = (
            cp.connection.execute(
                select(read_events)
                .where(read_events.c.scope == cp.scope)
                .order_by(read_events.c.sequence.desc())
                .limit(1)
            )
            .mappings()
            .one_or_none()
        )
        revision, dig, previous_hash = (
            binding["base_revision"],
            binding["base_digest"],
            binding["root_hash"],
        )
        if tip is not None:
            require(
                count == tip["sequence"] + 1
                and digest(_event_body(tip)) == tip["hash"]
                and tip["event_id"]
                == str(uuid5(cp.journal, "demo-read-audit:" + str(tip["sequence"])))
                and tip["revision"] == binding["base_revision"] + count
                and tip["token"] == tip["action"]["token"],
                "READ_AUDIT_HISTORY_INVALID",
            )
            if count > 1:
                previous_hash = cp.connection.execute(
                    select(read_events.c.hash).where(
                        read_events.c.scope == cp.scope, read_events.c.sequence == count - 2
                    )
                ).scalar_one()
            require(tip["previous_hash"] == previous_hash, "READ_AUDIT_HISTORY_INVALID")
            revision, dig = tip["revision"], tip["checkpoint_digest"]
        else:
            require(count == 0, "READ_AUDIT_HISTORY_INVALID")
        require(
            cp.revision == revision and cp.digest == dig and digest(snapshot(c)) == dig,
            "READ_AUDIT_UNRECORDED_CHANGE",
        )
        return binding, tip, count

    def append(
        self, cp: CheckpointContext, c: sqlite3.Connection, before: Any, action: dict[str, Any]
    ) -> None:
        # Called before SQLite commits and in the same PG transaction as checkpoint append.
        binding = self._binding(cp)
        count = cp.connection.execute(
            select(func.count()).select_from(read_events).where(read_events.c.scope == cp.scope)
        ).scalar_one()
        require(count < MAX_RECORDS, "READ_AUDIT_RECORD_LIMIT")
        if action["kind"] == "RESERVE":
            pending = sum(row[4] is None for row in before[2])
            require(count + pending + 2 <= MAX_RECORDS, "READ_AUDIT_COMPLETION_CREDIT_REQUIRED")
        after = snapshot(c)
        if action["kind"] == "CLOCK_REBASE":
            require(
                action["recovery"]["before_revision"] == cp.revision,
                "READ_RECOVERY_REVISION_INVALID",
            )
        validate_transition(before, after, action)
        previous_hash = (
            binding["root_hash"]
            if count == 0
            else cp.connection.execute(
                select(read_events.c.hash).where(
                    read_events.c.scope == cp.scope, read_events.c.sequence == count - 1
                )
            ).scalar_one()
        )
        row = dict(
            scope=cp.scope,
            sequence=count,
            event_id=str(uuid5(cp.journal, "demo-read-audit:" + str(count))),
            revision=cp.revision + 1,
            checkpoint_digest=digest(after),
            token=action["token"],
            action=action,
            state=after,
            previous_hash=previous_hash,
        )
        row["hash"] = digest(row)
        cp.connection.execute(insert(read_events).values(**row))

    def request(self, cp: CheckpointContext, token: str) -> Any:
        rows = (
            cp.connection.execute(
                select(read_events)
                .where(read_events.c.scope == cp.scope, read_events.c.token == token)
                .order_by(read_events.c.sequence)
            )
            .mappings()
            .all()
        )
        require(
            len(rows) == 1 and rows[0]["action"]["kind"] == "RESERVE",
            "READ_AUDIT_REQUEST_NOT_PENDING",
        )
        return rows[0]["action"]

    def _verified_history(
        self, cp: CheckpointContext, c: sqlite3.Connection
    ) -> tuple[list[dict[str, Any]], Any]:
        binding, _, _ = self.verify_tip(cp, c)
        state = binding["base_state"]
        previous = binding["root_hash"]
        requests: dict[str, Any] = {}
        rows = (
            cp.connection.execute(
                select(read_events)
                .where(read_events.c.scope == cp.scope)
                .order_by(read_events.c.sequence)
            )
            .mappings()
            .all()
        )
        for index, row in enumerate(rows):
            require(
                row["sequence"] == index
                and row["revision"] == binding["base_revision"] + index + 1
                and row["previous_hash"] == previous
                and digest(_event_body(row)) == row["hash"]
                and row["token"] == row["action"]["token"]
                and row["event_id"] == str(uuid5(cp.journal, "demo-read-audit:" + str(index))),
                "READ_AUDIT_HISTORY_INVALID",
            )
            witness = cp.connection.execute(
                select(checkpoints.c.digest).where(
                    checkpoints.c.scope == cp.scope, checkpoints.c.revision == row["revision"]
                )
            ).scalar_one()
            require(
                witness == row["checkpoint_digest"] == digest(row["state"]),
                "READ_AUDIT_CHECKPOINT_MISMATCH",
            )
            action = row["action"]
            if action["kind"] == "RESERVE":
                require(action["token"] not in requests, "READ_AUDIT_REQUEST_DUPLICATED")
                requests[action["token"]] = action
            elif action["kind"] == "FINISH":
                reserved = requests.get(action["token"])
                require(
                    reserved is not None
                    and reserved["path"] == action["path"]
                    and reserved["cost"] == action["cost"],
                    "READ_AUDIT_ROUTE_CHANGED",
                )
            if action["kind"] == "CLOCK_REBASE":
                require(
                    action["recovery"]["before_revision"] == row["revision"] - 1,
                    "READ_RECOVERY_REVISION_INVALID",
                )
            validate_transition(state, row["state"], action)
            state, previous = row["state"], row["hash"]
        require(digest(state) == digest(snapshot(c)), "READ_AUDIT_UNRECORDED_CHANGE")
        return [dict(row) for row in rows], state

    def review(self, shared: "AuditedDemoReadJournal") -> dict[str, Any]:
        require_runtime_cost_contract(MODELED_TP_ROUTE_COSTS)
        reader = copy.copy(shared)
        reader.read_only = True
        with reader._transaction() as (c, meta, now):
            cp = reader.checkpoint.get()
            assert cp is not None
            rows, state = self._verified_history(cp, c)
            count = len(rows)
            from packages.persistence.demo_read_recovery import verify_recovery_backups

            verify_recovery_backups(shared.directory, rows)
            pending = sum(row[4] is None for row in state[2])
            return dict(
                read_activity_review="AUDITED_READ_ACTIVITY_VERIFIED_KEEP_PAUSED",
                read_audit_chain_verified=True,
                audit_events=count,
                successful_reads=sum(
                    row["action"]["kind"] == "FINISH"
                    and row["action"]["sent"]
                    and row["action"]["status"] is not None
                    and 200 <= row["action"]["status"] < 300
                    and row["state"][1][0][8] == 0
                    for row in rows
                ),
                failed_response_reads=sum(
                    row["action"]["kind"] == "FINISH"
                    and row["action"]["sent"]
                    and row["action"]["status"] is not None
                    and not 200 <= row["action"]["status"] < 300
                    and row["state"][1][0][8] == 0
                    for row in rows
                ),
                known_unsent_requests=sum(
                    row["action"]["kind"] == "FINISH" and not row["action"]["sent"] for row in rows
                ),
                shared_total_weight=meta["total"],
                pending_requests=pending,
                local_read_activity_complete=pending == 0,
                startup_ready=now - meta["start"] >= 70.001,
                financial_proof_verified=False,
                account_order_usage_verified=False,
                actual_ip_capacity_reserved=False,
                order_writes=0,
                execution_approved=False,
                orders_enabled=False,
                live_trading_enabled=False,
            )


@dataclass
class _Action:
    kind: str
    path: str | None = None
    cost: int | None = None
    token: str | None = None
    status: int | None = None
    weight: int | None = None
    sent: bool = False
    observe: bool = True


class AuditedDemoReadJournal(AnchoredSharedWeightJournal):
    def __init__(self, *args: Any, audit: ReadAuditStore, **kwargs: Any):
        self.audit = audit
        self._backup_witness: str | None = None
        self._action: ContextVar[_Action | None] = ContextVar("demo_read_action", default=None)
        self._before: ContextVar[Any] = ContextVar("demo_read_before", default=None)
        writable = not kwargs.pop("read_only", False)
        kwargs["read_only"] = True
        require(
            kwargs.get("anchors") is not None and kwargs["anchors"].engine is audit.engine,
            "READ_AUDIT_ANCHOR_ENGINE_REQUIRED",
        )
        require_runtime_cost_contract(MODELED_TP_ROUTE_COSTS)
        super().__init__(*args, **kwargs)
        audit.review(self)
        self.read_only = not writable

    @contextmanager
    def _transaction(self) -> Iterator[tuple[sqlite3.Connection, dict[str, Any], float]]:
        marker = self._before.set(None)
        try:
            with super()._transaction() as value:
                yield value
        finally:
            self._before.reset(marker)

    def _verify_checkpoint(self, c: sqlite3.Connection) -> None:
        require(self.audit.engine is self.anchors.engine, "READ_AUDIT_ANCHOR_ENGINE_REQUIRED")
        super()._verify_checkpoint(c)
        cp = self.checkpoint.get()
        assert cp is not None
        self.audit.verify_tip(cp, c)
        # An old live object must not ignore an interrupted recovery or a changed
        # backup. Hash unchanged artifacts once, then compare bounded file witnesses.
        from packages.persistence.demo_read_recovery import (
            recovery_layout_witness,
            verify_recovery_backups,
        )

        recovery_rows = [
            dict(row)
            for row in cp.connection.execute(
                select(read_events.c.action, read_events.c.checkpoint_digest).where(
                    read_events.c.scope == self.scope,
                    read_events.c.action["kind"].astext == "CLOCK_REBASE",
                )
            ).mappings()
        ]
        witness = recovery_layout_witness(self.directory, recovery_rows)
        if self._backup_witness != witness:
            verify_recovery_backups(self.directory, recovery_rows)
            require(
                recovery_layout_witness(self.directory, recovery_rows) == witness,
                "READ_RECOVERY_BACKUP_INVALID",
            )
            self._backup_witness = witness
        self._before.set(snapshot(c))

    def _stage_checkpoint(self, c: sqlite3.Connection) -> None:
        action = self._action.get()
        cp = self.checkpoint.get()
        before = self._before.get()
        require(
            action is not None and cp is not None and before is not None,
            "READ_AUDIT_ROUTE_CONTEXT_REQUIRED",
        )
        assert action is not None and cp is not None
        after = snapshot(c)
        if action.kind == "RESERVE":
            added = _rows(after).keys() - _rows(before).keys()
            require(len(added) == 1, "READ_AUDIT_RESERVATION_INVALID")
            action.token = next(iter(added))
        require(digest(before) != digest(after), "READ_AUDIT_TRANSITION_INVALID")
        self.audit.append(cp, c, before, asdict(action))
        super()._stage_checkpoint(c)

    def reserve_read(self, path: str, cost: int) -> str:
        require_runtime_cost_contract(MODELED_TP_ROUTE_COSTS)
        require(
            type(cost) is int and MODELED_TP_ROUTE_COSTS.get(("GET", path)) == cost,
            "READ_AUDIT_ROUTE_INVALID",
        )
        marker = self._action.set(_Action("RESERVE", path, cost))
        try:
            return super().reserve(cost, "ORDINARY")
        finally:
            self._action.reset(marker)

    def reserve(self, weight: int, lane: str) -> str:
        require(False, "READ_AUDIT_ROUTE_CONTEXT_REQUIRED")
        raise AssertionError("unreachable")

    def _finish_read(
        self, token: str, *, weight: int | None, status: int | None, sent: bool, observe: bool
    ) -> None:
        require(
            isinstance(token, str)
            and str(UUID(token)) == token
            and type(sent) is bool
            and (weight is None or type(weight) is int and 0 <= weight < 10**12)
            and (status is None or type(status) is int and 100 <= status <= 599),
            "READ_AUDIT_RESPONSE_ARGUMENT_INVALID",
        )
        # Resolve route from the immutable reservation while holding the same scope.
        with self.anchors.scope(self.scope, self.identity, read_only=True) as cp:
            assert isinstance(cp, CheckpointContext)
            old = self.audit.request(cp, token)
        action = _Action("FINISH", old["path"], old["cost"], token, status, weight, sent, observe)
        marker = self._action.set(action)
        try:
            super()._finish(
                token, used_weight=weight, status=status, sent=sent, observe_weight=observe
            )
        finally:
            self._action.reset(marker)

    def finish(
        self, token: str, *, used_weight: int | None, status: int | None, sent: bool = True
    ) -> None:
        self._finish_read(token, weight=used_weight, status=status, sent=sent, observe=True)

    def finish_unobserved(self, token: str, *, status: int) -> None:
        self._finish_read(token, weight=None, status=status, sent=True, observe=False)

    def stop_unknown(self) -> None:
        marker = self._action.set(_Action("STOP", sent=True))
        try:
            super().stop_unknown()
        finally:
            self._action.reset(marker)

    def view(self) -> dict[str, Any]:
        reader = copy.copy(self)
        reader.read_only = True
        return SharedWeightJournal.view(reader)
