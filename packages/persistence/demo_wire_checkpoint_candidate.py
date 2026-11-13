"""Explicit synthetic PG/file anchor; financial prepare receipt shares its PG commit.

PG account lock -> scope advisory lock -> private files. Files fsync before PG
commit; a gap denies reopening/review. Whole DB+file rollback is not external proof.
"""

import hashlib
import os
import threading
from contextvars import ContextVar
from datetime import UTC
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

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
from sqlalchemy.engine import Connection, Engine

from packages.persistence.demo import DemoStore, accounts, outbox
from packages.persistence.demo import events as financial_events
from packages.persistence.demo_order_quota import LocalQuotaStopped, canonical
from packages.persistence.demo_read_audit import digest, require
from packages.persistence.demo_read_recovery import (
    _file,
    _private_directory,
    _sync_directory,
    _write_new,
)
from packages.persistence.demo_response_evidence import _append_line, _line
from services.execution.demo_models import DemoLedger
from services.execution.demo_request_outcome_candidate import _json, quantity
from services.execution.demo_submission_link_candidate import KINDS, IntentPin, _intent
from services.execution.demo_wire_session_contract_candidate import (
    MAX_BYTES,
    MAX_RECORDS,
    SyntheticWireJournal,
    _records,
    _replay,
    _scope,
)
from services.execution.demo_wire_session_contract_candidate import (
    POLICY as WIRE_POLICY,
)
from services.execution.models import Lease

POLICY = "SYNTHETIC_FINANCIAL_WIRE_CHECKPOINT_V2"
FINANCIAL_KIND = "DEMO_SYNTHETIC_WIRE_PREPARED"
PREFIX = "SYNTHETIC_WIRE_V2:"
MAX_PAYLOAD = 1048576
metadata = MetaData()
installations = Table(
    "wire_checkpoint_installation_v2",
    metadata,
    Column("name", String(64), primary_key=True),
    Column("identity", String(36), nullable=False),
)
bindings = Table(
    "wire_checkpoint_bindings_v2",
    metadata,
    Column("scope", String(80), primary_key=True),
    Column("journal", String(36), nullable=False, unique=True),
    Column("account", ForeignKey(accounts.c.account_id), nullable=False),
    Column("installation", String(36), nullable=False),
    Column("wire_root_hash", String(64), nullable=False),
    Column("policy", String(64), nullable=False),
    Column("hash", String(64), nullable=False),
)
checkpoints = Table(
    "wire_checkpoint_events_v2",
    metadata,
    Column("scope", ForeignKey(bindings.c.scope), primary_key=True),
    Column("sequence", BigInteger, primary_key=True),
    Column("wire", JSONB, nullable=False),
    Column("financial_event", ForeignKey(financial_events.c.event_id), nullable=True, unique=True),
    Column("finance", JSONB, nullable=True),
    Column("source", JSONB, nullable=True),
    Column("previous_hash", String(64), nullable=False),
    Column("hash", String(64), nullable=False),
)
TABLES = (installations, bindings, checkpoints)


class CoupledDemoStore(DemoStore):
    def __init__(self, *args: Any, **kw: Any):
        super().__init__(*args, **kw)
        self._wire_stage: ContextVar[Any] = ContextVar(
            "candidate_wire_financial_stage", default=None
        )

    def _stage_change_audit(
        self,
        connection: Connection,
        event_id: str,
        lease: Lease,
        kind: str,
        before_state: dict[str, Any],
    ) -> None:
        stage = self._wire_stage.get()
        if stage is not None:
            require(kind == FINANCIAL_KIND, "WIRE_ANCHOR_FINANCIAL_CONTEXT_INVALID")
            stage(connection, event_id, before_state)


def _envelope(row: Any) -> dict[str, Any]:
    return dict(
        event_id=row["event_id"],
        account=row["account_id"],
        fence=row["fence"],
        as_of=row["as_of"].astimezone(UTC).isoformat(),
        kind=row["kind"],
        payload_sha256=hashlib.sha256(row["payload"].encode()).hexdigest(),
        topic="DEMO_AUDIT",
    )


def _event(c: Connection, event_id: str, account: str, *, lock: bool = False) -> Any:
    where = (financial_events.c.event_id == event_id, financial_events.c.account_id == account)
    size = c.execute(
        select(func.octet_length(financial_events.c.payload)).where(*where)
    ).scalar_one_or_none()
    require(
        type(size) is int and 0 < size <= MAX_PAYLOAD,
        "WIRE_ANCHOR_FINANCIAL_EVENT_MISSING_OR_LIMIT",
    )
    query = select(financial_events).where(*where)
    if lock:
        query = query.with_for_update()
    row = c.execute(query).mappings().one()
    topic = c.execute(
        select(outbox.c.topic).where(outbox.c.event_id == event_id)
    ).scalar_one_or_none()
    require(
        topic == "DEMO_AUDIT" and len(row["payload"].encode()) == size,
        "WIRE_ANCHOR_FINANCIAL_OUTBOX_OR_SIZE_INVALID",
    )
    return row


class WireCheckpointStore:
    def __init__(self, engine: Engine):
        require(engine.dialect.name == "postgresql", "WIRE_ANCHOR_POSTGRESQL_REQUIRED")
        self.engine = engine

    def provision(self, *, apply: bool = False) -> None:
        require(apply is True, "WIRE_ANCHOR_EXPLICIT_PROVISION_REQUIRED")
        with self.engine.begin() as c:
            for table in TABLES:
                table.create(c, checkfirst=False)
            c.execute(insert(installations).values(name=POLICY, identity=str(uuid4())))
            c.execute(
                text(
                    "CREATE FUNCTION wire_checkpoint_immutable_v2() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'wire_checkpoint_append_only'; END $$"
                )
            )
            for table in TABLES:
                c.execute(
                    text(
                        f"CREATE TRIGGER wire_checkpoint_immutable_v2 BEFORE UPDATE OR DELETE ON {table.name} FOR EACH ROW EXECUTE FUNCTION wire_checkpoint_immutable_v2()"
                    )
                )

    def _schema(self, c: Connection) -> str:
        rows = c.execute(
            text("""SELECT r.relname,t.tgname,t.tgtype,t.tgenabled,p.prosrc,p.prosecdef
        FROM pg_trigger t JOIN pg_class r ON r.oid=t.tgrelid JOIN pg_namespace n ON n.oid=r.relnamespace
        JOIN pg_proc p ON p.oid=t.tgfoid WHERE n.nspname=current_schema() AND NOT t.tgisinternal
        AND r.relname IN ('wire_checkpoint_installation_v2','wire_checkpoint_bindings_v2','wire_checkpoint_events_v2')""")
        ).all()
        require(
            len(rows) == 3
            and {r[0] for r in rows} == {t.name for t in TABLES}
            and all(
                r[1] == "wire_checkpoint_immutable_v2"
                and r[2] == 27
                and r[3] in {"O", "A"}
                and " ".join(r[4].split())
                == "BEGIN RAISE EXCEPTION 'wire_checkpoint_append_only'; END"
                and r[5] is False
                for r in rows
            ),
            "WIRE_ANCHOR_SCHEMA_INVALID",
        )
        installed = c.execute(select(installations)).mappings().all()
        require(
            len(installed) == 1 and installed[0]["name"] == POLICY,
            "WIRE_ANCHOR_INSTALLATION_INVALID",
        )
        identity = installed[0]["identity"]
        require(str(UUID(identity)) == identity, "WIRE_ANCHOR_INSTALLATION_INVALID")
        return str(identity)

    @staticmethod
    def _lock(c: Connection, scope: str, *, read_only: bool = False) -> None:
        c.execute(text("SET LOCAL lock_timeout = '2s'"))
        c.execute(text("SET LOCAL statement_timeout = '2s'"))
        key = int.from_bytes(
            hashlib.sha256((POLICY + scope).encode()).digest()[:8], "big", signed=True
        )
        name = "pg_advisory_xact_lock_shared" if read_only else "pg_advisory_xact_lock"
        c.execute(text(f"SELECT {name}(:key)"), dict(key=key))

    @staticmethod
    def witness(directory: Path) -> Path:
        return directory / "wire-checkpoint-v2.log"

    def _verify(
        self, c: Connection, directory: Path, scope: str, pending_finance: Any = None
    ) -> tuple[Any, list[Any], bytes, bytes]:
        installation = self._schema(c)
        binding = (
            c.execute(select(bindings).where(bindings.c.scope == scope)).mappings().one_or_none()
        )
        require(binding is not None, "WIRE_ANCHOR_BINDING_MISSING")
        assert binding is not None
        require(
            binding["installation"] == installation
            and binding["policy"] == POLICY
            and digest({k: v for k, v in binding.items() if k != "hash"}) == binding["hash"],
            "WIRE_ANCHOR_BINDING_INVALID",
        )
        raw = _file(directory / "wire-contract-v1.log", MAX_BYTES)
        wires = _records(raw)
        _replay(wires)
        root = wires[0]
        require(
            root["hash"] == binding["wire_root_hash"]
            and root["data"]["journal"] == binding["journal"]
            and root["data"]["scope"] == scope
            and root["data"]["account"] == binding["account"],
            "WIRE_ANCHOR_ROOT_INVALID",
        )
        rows = (
            c.execute(
                select(checkpoints)
                .where(checkpoints.c.scope == scope)
                .order_by(checkpoints.c.sequence)
                .limit(MAX_RECORDS + 1)
            )
            .mappings()
            .all()
        )
        require(len(rows) == len(wires) <= MAX_RECORDS, "WIRE_ANCHOR_CHECKPOINT_GAP")
        previous, expected = binding["hash"], b""
        expected_markers: dict[str, str] = {}
        for index, (row, wire) in enumerate(zip(rows, wires, strict=True)):
            require(
                row["sequence"] == index
                and row["wire"] == wire
                and row["previous_hash"] == previous
                and digest({k: v for k, v in row.items() if k != "hash"}) == row["hash"],
                "WIRE_ANCHOR_CHECKPOINT_INVALID",
            )
            mutation = wire["kind"] == "PREPARE" and wire["data"]["role"] != "READ"
            require(
                (row["financial_event"] is not None) is mutation
                and (row["finance"] is not None) is mutation
                and (row["source"] is not None) is mutation,
                "WIRE_ANCHOR_FINANCIAL_LINK_MISSING",
            )
            if mutation:
                self._verify_finance(c, binding, row)
                expected_markers[row["finance"]["marker"]] = row["finance"]["value"]
            expected += _line(dict(sequence=index, hash=row["hash"]))
            previous = row["hash"]
        if pending_finance is not None:
            expected_markers[pending_finance["marker"]] = pending_finance["value"]
        current = c.execute(
            select(accounts.c.state).where(accounts.c.account_id == binding["account"])
        ).scalar_one()
        operations = current.get("operations", {})
        require(
            isinstance(operations, dict) and len(operations) <= 8192, "WIRE_ANCHOR_OPERATION_LIMIT"
        )
        found: dict[str, str] = {}
        for key, value in operations.items():
            if key.startswith(PREFIX):
                require(isinstance(value, str), "WIRE_ANCHOR_MARKER_INVALID")
                payload = _json(value.encode())
                if payload.get("journal") == binding["journal"] or payload.get("scope") == scope:
                    require(
                        payload.get("journal") == binding["journal"]
                        and payload.get("scope") == scope,
                        "WIRE_ANCHOR_MARKER_BINDING_INVALID",
                    )
                    found[key] = value
        require(found == expected_markers, "WIRE_ANCHOR_FINANCIAL_MARKER_COVERAGE_MISMATCH")
        witness = _file(self.witness(directory), MAX_BYTES)
        require(witness == expected, "WIRE_ANCHOR_WITNESS_MISMATCH")
        return binding, list(rows), raw, witness

    def _verify_finance(self, c: Connection, binding: Any, checkpoint: Any) -> None:
        finance, source, wire = checkpoint["finance"], checkpoint["source"], checkpoint["wire"]
        row = _event(c, checkpoint["financial_event"], binding["account"])
        data = _json(row["payload"].encode())
        marker = PREFIX + wire["data"]["operation"]
        require(
            finance["value"]
            == canonical(
                dict(scope=binding["scope"], journal=binding["journal"], wire_hash=wire["hash"])
            ),
            "WIRE_ANCHOR_FINANCIAL_MARKER_VALUE_INVALID",
        )
        require(
            row["kind"] == FINANCIAL_KIND
            and row["account_id"] == binding["account"]
            and _envelope(row) == finance["event"]
            and digest(data) == finance["after_digest"]
            and finance["marker"] == marker
            and data["operations"].get(marker) == finance["value"],
            "WIRE_ANCHOR_FINANCIAL_WITNESS_INVALID",
        )
        data["operations"].pop(marker)
        require(digest(data) == finance["before_digest"], "WIRE_ANCHOR_FINANCIAL_DELTA_INVALID")
        current = c.execute(
            select(accounts.c.state).where(accounts.c.account_id == binding["account"])
        ).scalar_one()
        require(
            current.get("operations", {}).get(marker) == finance["value"],
            "WIRE_ANCHOR_FINANCIAL_MARKER_MISSING",
        )
        original = _event(c, source["event_id"], binding["account"])
        require(
            _envelope(original) == source
            and digest(source) == wire["data"]["intent_reference"]["envelope_sha256"]
            and original["kind"] == KINDS[wire["data"]["role"]],
            "WIRE_ANCHOR_SOURCE_PIN_MISMATCH",
        )

    @staticmethod
    def _checkpoint(
        binding: Any, previous: Any, wire: dict[str, Any], finance: Any = None, source: Any = None
    ) -> dict[str, Any]:
        row = dict(
            scope=binding["scope"],
            sequence=wire["sequence"],
            wire=wire,
            financial_event=None if finance is None else finance["event"]["event_id"],
            finance=finance,
            source=source,
            previous_hash=previous,
        )
        row["hash"] = digest(row)
        return row

    def review(self, directory: Path, scope: str, journal: UUID, account: str) -> dict[str, Any]:
        _scope(account, scope)
        _private_directory(directory)
        with (
            self.engine.connect().execution_options(isolation_level="REPEATABLE READ") as c,
            c.begin(),
        ):
            c.execute(text("SET TRANSACTION READ ONLY"))
            self._lock(c, scope, read_only=True)
            binding, rows, _, _ = self._verify(c, directory, scope)
            require(
                binding["journal"] == str(journal) and binding["account"] == account,
                "WIRE_ANCHOR_EXPECTED_BINDING_MISMATCH",
            )
            state = _replay([r["wire"] for r in rows])
            return dict(
                wire_checkpoint_review="PG_FILE_FINANCIAL_LINK_VERIFIED_SYNTHETIC_ONLY_KEEP_PAUSED",
                records=len(rows),
                financial_prepares=sum(r["finance"] is not None for r in rows),
                pending_operations=sum(
                    o["phase"] != "OBSERVED" for o in state["operations"].values()
                ),
                uncertain_outcome=state["unknown"],
                pg_file_checkpoint_matches=True,
                financial_prepare_transaction_coupled=any(r["finance"] is not None for r in rows),
                whole_system_rollback_protection_verified=False,
                original_historical_intent_authenticated=False,
                remote_account_identity_verified=False,
                real_wire_submission_verified=False,
                process_restart_resumption_supported=False,
                request_states_changed=False,
                archive_authorized=False,
                order_writes=0,
                actual_http_attempts=0,
                execution_approved=False,
                orders_enabled=False,
                live_trading_enabled=False,
            )


class AnchoredSyntheticWireJournal(SyntheticWireJournal):
    anchors: WireCheckpointStore
    store: CoupledDemoStore
    lease: Lease
    journal_id: UUID
    _configuration: tuple[Any, ...]

    @staticmethod
    def _live(c: Connection, store: DemoStore, lease: Lease) -> Any:
        c.execute(text("SET LOCAL lock_timeout = '2s'"))
        row = (
            c.execute(
                select(accounts).where(accounts.c.account_id == lease.account_id).with_for_update()
            )
            .mappings()
            .one()
        )
        require(
            row["owner"] == str(lease.owner)
            and row["fence"] == lease.fence
            and row["lease_until"] is not None
            and row["lease_until"] > store.now()
            and row["state"].get("paused") is True
            and row["state"].get("mode") == "DEMO",
            "WIRE_ANCHOR_PAUSED_LIVE_LEASE_REQUIRED",
        )
        return row

    @classmethod
    def create_anchored(
        cls,
        directory: Path,
        scope: str,
        store: CoupledDemoStore,
        lease: Lease,
        anchors: WireCheckpointStore,
        *,
        enable_synthetic_contract: bool = False,
    ) -> "AnchoredSyntheticWireJournal":
        require(enable_synthetic_contract is True, "WIRE_ANCHOR_DISABLED")
        require(
            type(store) is CoupledDemoStore and store.engine is anchors.engine,
            "WIRE_ANCHOR_STORE_ENGINE_MISMATCH",
        )
        lease = lease.model_copy(deep=True)
        _scope(lease.account_id, scope)
        _private_directory(directory)
        obj = cls()
        obj.directory, obj.path = directory, directory / "wire-contract-v1.log"
        obj.store, obj.lease, obj.anchors = store, lease.model_copy(deep=True), anchors
        obj._configuration = (
            store,
            anchors,
            store.engine,
            obj.lease.account_id,
            str(obj.lease.owner),
            obj.lease.fence,
        )
        obj._pid, obj._poisoned, obj._lock = os.getpid(), False, threading.RLock()
        obj.journal_id = uuid4()
        root = dict(
            sequence=0,
            previous_hash="0" * 64,
            kind="ROOT",
            data=dict(
                policy=WIRE_POLICY,
                environment="SYNTHETIC_ONLY",
                journal=str(obj.journal_id),
                account=lease.account_id,
                scope=scope,
            ),
        )
        root["hash"] = digest(root)
        obj._rows, obj._raw = [root], _line(root)
        with anchors.engine.begin() as c:
            obj._live(c, store, lease)
            anchors._lock(c, scope)
            installation = anchors._schema(c)
            require(
                c.execute(select(bindings.c.scope).where(bindings.c.scope == scope)).first()
                is None,
                "WIRE_ANCHOR_ALREADY_BOUND_NO_REOPEN",
            )
            binding = dict(
                scope=scope,
                journal=str(obj.journal_id),
                account=lease.account_id,
                installation=installation,
                wire_root_hash=root["hash"],
                policy=POLICY,
            )
            binding["hash"] = digest(binding)
            c.execute(insert(bindings).values(**binding))
            checkpoint = anchors._checkpoint(binding, binding["hash"], root)
            c.execute(insert(checkpoints).values(**checkpoint))
            _write_new(obj.path, obj._raw)
            _write_new(anchors.witness(directory), _line(dict(sequence=0, hash=checkpoint["hash"])))
            _sync_directory(directory)
            obj._live(c, store, lease)
        return obj

    def _persist(
        self,
        c: Connection,
        kind: str,
        data: dict[str, Any],
        finance: Any = None,
        source: Any = None,
    ) -> tuple[Any, bytes]:
        self.anchors._lock(c, self._rows[0]["data"]["scope"])
        binding, old, raw, witness = self.anchors._verify(
            c, self.directory, self._rows[0]["data"]["scope"], pending_finance=finance
        )
        require(
            binding["account"] == self.lease.account_id
            and binding["journal"] == str(self.journal_id),
            "WIRE_ANCHOR_JOURNAL_BINDING_CHANGED",
        )
        require(raw == self._raw, "WIRE_ANCHOR_MEMORY_TIP_MISMATCH")
        wire = dict(
            sequence=len(self._rows), previous_hash=self._rows[-1]["hash"], kind=kind, data=data
        )
        wire["hash"] = digest(wire)
        _replay(self._rows + [wire])
        line = _line(wire)
        require(
            len(self._rows) < MAX_RECORDS and len(raw) + len(line) <= MAX_BYTES, "WIRE_ANCHOR_LIMIT"
        )
        checkpoint = self.anchors._checkpoint(binding, old[-1]["hash"], wire, finance, source)
        if finance is not None:
            self.anchors._verify_finance(c, binding, checkpoint)
        c.execute(insert(checkpoints).values(**checkpoint))
        _append_line(self.path, raw, line)
        _append_line(
            self.anchors.witness(self.directory),
            witness,
            _line(dict(sequence=wire["sequence"], hash=checkpoint["hash"])),
        )
        return wire, raw + line

    def _append(self, kind: str, data: dict[str, Any]) -> None:
        require(not self._poisoned and self._pid == os.getpid(), "WIRE_ANCHOR_WRITER_BLOCKED")
        try:
            require(
                self._configuration
                == (
                    self.store,
                    self.anchors,
                    self.store.engine,
                    self.lease.account_id,
                    str(self.lease.owner),
                    self.lease.fence,
                )
                and self.store.engine is self.anchors.engine,
                "WIRE_ANCHOR_CONFIGURATION_CHANGED",
            )
            if kind == "PREPARE" and data["role"] != "READ":
                wire, raw = self._financial_prepare(data)
            else:
                with self.anchors.engine.begin() as c:
                    self._live(c, self.store, self.lease)
                    wire, raw = self._persist(c, kind, data)
                    self._live(c, self.store, self.lease)
            self._rows.append(wire)
            self._raw = raw
        except Exception:
            self._poisoned = True
            raise LocalQuotaStopped("WIRE_ANCHOR_COMMIT_FAILED_NO_RETRY") from None

    def _financial_prepare(self, data: dict[str, Any]) -> tuple[Any, bytes]:
        role, params = data["role"], data["params"]
        request_id = params.get("newClientOrderId", params.get("clientAlgoId"))
        pin = IntentPin(
            UUID(data["intent_reference"]["event_id"]), data["intent_reference"]["envelope_sha256"]
        )
        source, order, expectation = _intent(
            self.store.engine, self.lease.account_id, pin.event_id, role, request_id
        )
        require(digest(source) == pin.envelope_sha256, "WIRE_ANCHOR_SOURCE_PIN_MISMATCH")
        if expectation is not None:
            require(params["side"] == expectation.side, "WIRE_ANCHOR_WIRE_TERMS_MISMATCH")
            if role == "ENTRY":
                require(
                    quantity(params["quantity"]) == quantity(expectation.quantity)
                    and quantity(params["price"])
                    == quantity(order["signal"]["suggested_limit_price"])
                    and params["timeInForce"] == "GTC",
                    "WIRE_ANCHOR_WIRE_TERMS_MISMATCH",
                )
            else:
                require(
                    quantity(params["triggerPrice"])
                    == quantity(order["signal"]["initial_stop_price"]),
                    "WIRE_ANCHOR_WIRE_TERMS_MISMATCH",
                )
        else:
            require(
                params["side"] == ("SELL" if order["signal"]["direction"] == "LONG" else "BUY"),
                "WIRE_ANCHOR_EXIT_DIRECTION_MISMATCH",
            )
        marker = PREFIX + data["operation"]
        prospective = dict(
            sequence=len(self._rows),
            previous_hash=self._rows[-1]["hash"],
            kind="PREPARE",
            data=data,
        )
        value = canonical(
            dict(
                scope=self._rows[0]["data"]["scope"],
                journal=str(self.journal_id),
                wire_hash=digest(prospective),
            )
        )
        result: list[Any] = []

        def stage(ledger: DemoLedger) -> None:
            require(
                ledger.paused is True
                and ledger.requests.get(request_id) == "SUBMITTING"
                and marker not in ledger.operations,
                "WIRE_ANCHOR_FINANCIAL_INTENT_NOT_CURRENT",
            )
            ledger.operations[marker] = value

        def audit(c: Connection, event_id: str, before: dict[str, Any]) -> None:
            original = _event(c, str(pin.event_id), self.lease.account_id, lock=True)
            require(_envelope(original) == source, "WIRE_ANCHOR_SOURCE_CHANGED_DURING_PREPARE")
            row = _event(c, event_id, self.lease.account_id)
            after = _json(row["payload"].encode())
            cleaned = _json(row["payload"].encode())
            require(
                cleaned["operations"].pop(marker, None) == value and cleaned == before,
                "WIRE_ANCHOR_ONLY_MARKER_CHANGE_REQUIRED",
            )
            finance = dict(
                event=_envelope(row),
                marker=marker,
                value=value,
                before_digest=digest(before),
                after_digest=digest(after),
            )
            result.extend(self._persist(c, "PREPARE", data, finance, source))
            self._live(c, self.store, self.lease)

        token = self.store._wire_stage.set(audit)
        try:
            self.store.change(self.lease, FINANCIAL_KIND, stage)
        finally:
            self.store._wire_stage.reset(token)
        require(len(result) == 2, "WIRE_ANCHOR_FINANCIAL_HOOK_REQUIRED")
        return result[0], result[1]
