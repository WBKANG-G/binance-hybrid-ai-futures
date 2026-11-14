"""Uninstalled V2 evidence candidate; explicit provisioning, no backfill or repair.

Existing V1 audit rows and checkpoints stay unchanged. Sidecar evidence appends in
that same PG transaction and has a separate fsynced file witness. Commit gaps deny
reopen; this module never refunds, resends or grants archive/execution authority.
"""

import copy
import hashlib
import math
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import (
    BigInteger,
    Column,
    ForeignKey,
    ForeignKeyConstraint,
    MetaData,
    String,
    Table,
    insert,
    inspect,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection, Engine

from integrations.binance_usdm.rest import RESTResponse
from packages.persistence.demo_order_quota import canonical
from packages.persistence.demo_read_audit import (
    AuditedDemoReadJournal,
    ReadAuditStore,
    _event_body,
    digest,
    read_bindings,
    read_events,
    require,
)
from packages.persistence.demo_read_recovery import (
    _file,
    _sync_directory,
    _write_new,
)
from packages.persistence.demo_shared_checkpoint import (
    CheckpointContext,
    SharedWeightAnchorStore,
    checkpoints,
)
from packages.persistence.demo_shared_weight import SharedWeightJournal
from services.execution.demo_query_contract import (
    PUBLIC,
    SIGNING,
    integer,
    require_public_query,
    require_signed_query,
    require_wire_query,
)

POLICY = "ANCHORED_DEMO_RESPONSE_EVIDENCE_V2"
MAX_BODY = 4_194_304
MAX_RECORDS = 1024
metadata = MetaData()
bindings = Table(
    "read_response_evidence_bindings_v2",
    metadata,
    Column("scope", ForeignKey(read_bindings.c.scope), primary_key=True),
    Column("journal", String(36), nullable=False),
    Column("installation", String(36), nullable=False),
    Column("account", String(80), nullable=False),
    Column("policy", String(64), nullable=False),
    Column("base_sequence", BigInteger, nullable=False),
    Column("base_audit_hash", String(64), nullable=False),
    Column("base_revision", BigInteger, nullable=False),
    Column("base_digest", String(64), nullable=False),
    Column("root_hash", String(64), nullable=False),
)
events = Table(
    "read_response_evidence_events_v2",
    metadata,
    Column("scope", ForeignKey(bindings.c.scope), primary_key=True),
    Column("sequence", BigInteger, primary_key=True),
    Column("audit_hash", String(64), nullable=False),
    Column("revision", BigInteger, nullable=False),
    Column("checkpoint_digest", String(64), nullable=False),
    Column("phase", String(10), nullable=False),
    Column("request_token", String(36), nullable=False),
    Column("request", JSONB, nullable=False),
    Column("response", JSONB, nullable=True),
    Column("response_state", String(24), nullable=False),
    Column("previous_hash", String(64), nullable=False),
    Column("hash", String(64), nullable=False),
    ForeignKeyConstraint(["scope", "sequence"], [read_events.c.scope, read_events.c.sequence]),
)


def request_metadata(
    method: str, path: str, query: SecretStr, key: SecretStr | None
) -> dict[str, Any]:
    require(method == "GET", "RESPONSE_EVIDENCE_GET_ONLY")
    require_wire_query(method, path, query, key)
    values = dict(
        parse_qsl(
            query.get_secret_value(), keep_blank_values=True, strict_parsing=True, max_num_fields=32
        )
    )
    result = dict(
        method=method,
        path=path,
        params={k: v for k, v in values.items() if k not in SIGNING},
        timestamp=values.get("timestamp"),
        recvWindow=values.get("recvWindow"),
        authenticated=key is not None,
    )
    validate_request(result)
    return result


def validate_request(value: Any) -> None:
    require(
        isinstance(value, dict)
        and set(value) == {"method", "path", "params", "timestamp", "recvWindow", "authenticated"},
        "RESPONSE_EVIDENCE_REQUEST_INVALID",
    )
    require(
        value["method"] == "GET"
        and isinstance(value["path"], str)
        and isinstance(value["params"], dict)
        and type(value["authenticated"]) is bool,
        "RESPONSE_EVIDENCE_REQUEST_INVALID",
    )
    if value["path"] in PUBLIC:
        require(
            value["authenticated"] is False
            and value["timestamp"] is None
            and value["recvWindow"] is None,
            "RESPONSE_EVIDENCE_REQUEST_INVALID",
        )
        require_public_query(value["path"], value["params"])
    else:
        require(
            value["authenticated"] is True
            and isinstance(value["timestamp"], str)
            and integer(value["timestamp"])
            and value["recvWindow"] == "5000",
            "RESPONSE_EVIDENCE_REQUEST_INVALID",
        )
        require_signed_query("GET", value["path"], value["params"])


def response_metadata(value: RESTResponse) -> dict[str, Any]:
    require(
        isinstance(value, RESTResponse)
        and type(value.status) is int
        and 100 <= value.status <= 599
        and isinstance(value.body, bytes)
        and len(value.body) <= MAX_BODY,
        "RESPONSE_EVIDENCE_RESPONSE_INVALID",
    )
    return dict(
        status=value.status,
        body_bytes=len(value.body),
        body_sha256=hashlib.sha256(value.body).hexdigest(),
    )


def validate_response(value: Any) -> None:
    require(
        isinstance(value, dict)
        and set(value) == {"status", "body_bytes", "body_sha256"}
        and type(value["status"]) is int
        and 100 <= value["status"] <= 599
        and type(value["body_bytes"]) is int
        and 0 <= value["body_bytes"] <= MAX_BODY
        and isinstance(value["body_sha256"], str)
        and len(value["body_sha256"]) == 64
        and all(c in "0123456789abcdef" for c in value["body_sha256"]),
        "RESPONSE_EVIDENCE_RESPONSE_INVALID",
    )


@dataclass
class Capture:
    request: dict[str, Any] = field(repr=False)
    response: dict[str, Any] | None = field(default=None, repr=False)
    token: str | None = None
    wire_started: bool = False


def _body(row: Any, root: bool = False) -> dict[str, Any]:
    return {k: v for k, v in dict(row).items() if k != ("root_hash" if root else "hash")}


def _line(value: Any) -> bytes:
    return (canonical(value) + "\n").encode()


def _append_line(path: Path, previous: bytes, line: bytes) -> None:
    require(_file(path, 524288) == previous, "RESPONSE_EVIDENCE_WITNESS_CHANGED")
    stat = path.stat()
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        require(
            (os.fstat(fd).st_dev, os.fstat(fd).st_ino) == (stat.st_dev, stat.st_ino),
            "RESPONSE_EVIDENCE_WITNESS_REPLACED",
        )
        offset = 0
        while offset < len(line):
            count = os.write(fd, line[offset:])
            require(count > 0, "RESPONSE_EVIDENCE_WITNESS_WRITE_FAILED")
            offset += count
        os.fsync(fd)
    finally:
        os.close(fd)


def verify_clock(meta: Any, now: float, wall: datetime, boot: str) -> None:
    """A restart in the same boot may reopen; boot/drift changes require review.

    This check only denies. It never writes a stop flag, changes clock metadata,
    waits and retries, or authorizes V1 CLOCK_REBASE through the V2 event chain.
    """
    require(
        boot == meta["boot"]
        and type(now) in (float, int)
        and math.isfinite(now)
        and now >= meta["last"]
        and isinstance(wall, datetime)
        and wall.tzinfo is not None
        and wall.utcoffset() == timedelta(0)
        and abs((wall.timestamp() - meta["wall"]) - (now - meta["start"])) <= 1,
        "RESPONSE_EVIDENCE_CLOCK_REVIEW_REQUIRED",
    )


class ResponseEvidenceStore:
    def __init__(self, engine: Engine):
        require(engine.dialect.name == "postgresql", "RESPONSE_EVIDENCE_POSTGRESQL_REQUIRED")
        self.engine = engine
        self.audit = ReadAuditStore(engine)
        self.anchors = SharedWeightAnchorStore(engine)

    def provision(self, *, apply: bool = False) -> None:
        require(apply is True, "RESPONSE_EVIDENCE_EXPLICIT_PROVISION_REQUIRED")
        with self.engine.begin() as c:
            self._provision_on(c)

    def _provision_on(self, c: Connection) -> None:
        bindings.create(c, checkfirst=False)
        events.create(c, checkfirst=False)
        c.execute(
            text(
                "CREATE FUNCTION read_response_evidence_immutable_v2() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'response_evidence_append_only'; END $$"
            )
        )
        for t in (bindings, events):
            c.execute(
                text(
                    f"CREATE TRIGGER response_evidence_immutable_v2 BEFORE UPDATE OR DELETE ON {t.name} FOR EACH ROW EXECUTE FUNCTION read_response_evidence_immutable_v2()"
                )
            )

    @staticmethod
    def witness(directory: Path, scope: str) -> Path:
        return directory / (
            hashlib.sha256(scope.encode()).hexdigest() + ".response-evidence-v2.log"
        )

    def _schema(self, c: Connection) -> None:
        inspector = inspect(c)
        schema = c.execute(text("SELECT current_schema()")).scalar_one()
        for table in (bindings, events):
            columns = inspector.get_columns(table.name)
            require(
                [
                    (
                        x["name"],
                        str(x["type"]),
                        x["nullable"],
                        x.get("default"),
                        x.get("identity"),
                        x.get("computed"),
                    )
                    for x in columns
                ]
                == [
                    (x.name, x.type.compile(dialect=c.dialect), x.nullable, None, None, None)
                    for x in table.columns
                ],
                "RESPONSE_EVIDENCE_TABLE_SCHEMA_INVALID",
            )
            require(
                inspector.get_pk_constraint(table.name)["constrained_columns"]
                == [x.name for x in table.primary_key.columns]
                and not inspector.get_check_constraints(table.name)
                and not inspector.get_unique_constraints(table.name)
                and not inspector.get_indexes(table.name),
                "RESPONSE_EVIDENCE_TABLE_CONSTRAINTS_INVALID",
            )
            foreign = inspector.get_foreign_keys(table.name)
            expected = {
                (
                    tuple(x.parent.name for x in fk.elements),
                    tuple(x.column.name for x in fk.elements),
                    fk.referred_table.name,
                )
                for fk in table.foreign_key_constraints
            }
            actual = {
                (tuple(x["constrained_columns"]), tuple(x["referred_columns"]), x["referred_table"])
                for x in foreign
            }
            require(
                actual == expected
                and len(foreign) == len(expected)
                and all(
                    x.get("referred_schema") in (None, schema) and not x.get("options")
                    for x in foreign
                ),
                "RESPONSE_EVIDENCE_TABLE_FOREIGN_KEYS_INVALID",
            )
        rows = c.execute(
            text("""SELECT r.relname,t.tgname,t.tgtype,t.tgenabled,p.prosrc,p.prosecdef
         FROM pg_trigger t JOIN pg_class r ON r.oid=t.tgrelid JOIN pg_namespace n ON n.oid=r.relnamespace
         JOIN pg_proc p ON p.oid=t.tgfoid WHERE n.nspname=current_schema() AND NOT t.tgisinternal
         AND r.relname IN ('read_response_evidence_bindings_v2','read_response_evidence_events_v2')""")
        ).all()
        require(
            len(rows) == 2
            and {r[0] for r in rows} == {bindings.name, events.name}
            and all(
                r[1] == "response_evidence_immutable_v2"
                and r[2] == 27
                and r[3] in {"O", "A"}
                and " ".join(r[4].split())
                == "BEGIN RAISE EXCEPTION 'response_evidence_append_only'; END"
                and r[5] is False
                for r in rows
            ),
            "RESPONSE_EVIDENCE_SCHEMA_INVALID",
        )

    def bind(self, shared: AuditedDemoReadJournal, account: str, *, apply: bool = False) -> None:
        require(apply is True, "RESPONSE_EVIDENCE_EXPLICIT_BIND_REQUIRED")
        require(
            shared.anchors.engine is self.engine and shared.audit.engine is self.engine,
            "RESPONSE_EVIDENCE_ENGINE_MISMATCH",
        )
        require(
            isinstance(account, str) and account.startswith("DEMO-") and len(account) <= 80,
            "RESPONSE_EVIDENCE_ACCOUNT_REQUIRED",
        )
        reader = copy.copy(shared)
        reader.read_only = True
        with shared.anchors.scope(shared.scope, shared.identity, read_only=False) as cp:
            assert isinstance(cp, CheckpointContext)
            marker = reader.checkpoint.set(cp)
            try:
                with SharedWeightJournal._transaction(reader) as (local, meta, now):
                    self._bind_on(cp, shared.directory, account, local, meta, now)
            finally:
                reader.checkpoint.reset(marker)

    def _bind_on(
        self,
        cp: CheckpointContext,
        directory: Path,
        account: str,
        local: sqlite3.Connection,
        meta: dict[str, Any],
        now: float,
    ) -> None:
        """Attach in the caller's PG transaction; orphan witnesses are never repaired."""
        self._schema(cp.connection)
        old, state = self.audit._verified_history(cp, local)
        require(
            not any(r[4] is None for r in state[2]) and now - meta["start"] >= 70.001,
            "RESPONSE_EVIDENCE_COMPLETED_READY_HISTORY_REQUIRED",
        )
        existing = (
            cp.connection.execute(select(bindings).where(bindings.c.scope == cp.scope))
            .mappings()
            .one_or_none()
        )
        if existing is not None:
            self.verify(cp, directory, account, old)
            return
        path = self.witness(directory, cp.scope)
        require(
            not path.exists() and not path.is_symlink(),
            "RESPONSE_EVIDENCE_ORPHAN_WITNESS",
        )
        root = self.audit._binding(cp)
        record = dict(
            scope=cp.scope,
            journal=str(cp.journal),
            installation=root["installation"],
            account=account,
            policy=POLICY,
            base_sequence=len(old),
            base_audit_hash=old[-1]["hash"] if old else root["root_hash"],
            base_revision=cp.revision,
            base_digest=cp.digest,
        )
        record["root_hash"] = digest(record)
        cp.connection.execute(insert(bindings).values(**record))
        _write_new(path, _line(dict(root_hash=record["root_hash"])))
        _sync_directory(directory)

    def verify(
        self, cp: CheckpointContext, directory: Path, account: str, old: list[Any]
    ) -> tuple[Any, list[Any], dict[str, Any], bytes]:
        self._schema(cp.connection)
        binding = (
            cp.connection.execute(select(bindings).where(bindings.c.scope == cp.scope))
            .mappings()
            .one_or_none()
        )
        require(binding is not None, "RESPONSE_EVIDENCE_BINDING_MISSING")
        assert binding is not None
        root = self.audit._binding(cp)
        base = binding["base_sequence"]
        require(
            binding["journal"] == str(cp.journal)
            and binding["installation"] == root["installation"]
            and binding["account"] == account
            and binding["policy"] == POLICY
            and digest(_body(binding, True)) == binding["root_hash"]
            and type(base) is int
            and 0 <= base <= len(old),
            "RESPONSE_EVIDENCE_BINDING_INVALID",
        )
        require(
            binding["base_audit_hash"] == (old[base - 1]["hash"] if base else root["root_hash"])
            and binding["base_revision"]
            == (old[base - 1]["revision"] if base else root["base_revision"])
            and binding["base_digest"]
            == (old[base - 1]["checkpoint_digest"] if base else root["base_digest"]),
            "RESPONSE_EVIDENCE_BASE_INVALID",
        )
        rows = (
            cp.connection.execute(
                select(events).where(events.c.scope == cp.scope).order_by(events.c.sequence)
            )
            .mappings()
            .all()
        )
        require(
            len(rows) == len(old) - base and len(rows) <= MAX_RECORDS,
            "RESPONSE_EVIDENCE_COVERAGE_GAP",
        )
        expected = _line(dict(root_hash=binding["root_hash"]))
        previous = binding["root_hash"]
        pending: dict[str, Any] = {}
        for row, audit in zip(rows, old[base:], strict=True):
            require(
                row["sequence"] == audit["sequence"]
                and row["audit_hash"] == audit["hash"]
                and digest(_event_body(audit)) == audit["hash"]
                and row["revision"] == audit["revision"]
                and row["checkpoint_digest"] == audit["checkpoint_digest"]
                and row["phase"] == audit["action"]["kind"]
                and row["previous_hash"] == previous
                and digest(_body(row)) == row["hash"],
                "RESPONSE_EVIDENCE_HISTORY_INVALID",
            )
            self._transition(row, audit["action"], pending)
            expected += _line(dict(sequence=row["sequence"], hash=row["hash"]))
            previous = row["hash"]
        actual = _file(self.witness(directory, cp.scope), 524288)
        require(actual == expected, "RESPONSE_EVIDENCE_WITNESS_MISMATCH")
        return binding, list(rows), pending, actual

    @staticmethod
    def _transition(row: Any, action: Any, pending: dict[str, Any]) -> None:
        validate_request(row["request"])
        token = row["request_token"]
        require(
            isinstance(token, str) and str(UUID(token)) == token, "RESPONSE_EVIDENCE_TOKEN_INVALID"
        )
        kind = row["phase"]
        if kind == "RESERVE":
            require(
                action["token"] == token
                and row["request"]["path"] == action["path"]
                and token not in pending
                and row["response"] is None
                and row["response_state"] == "PENDING",
                "RESPONSE_EVIDENCE_RESERVE_INVALID",
            )
            pending[token] = row["request"]
        else:
            require(
                token in pending and pending[token] == row["request"],
                "RESPONSE_EVIDENCE_REQUEST_CHANGED",
            )
            if kind == "FINISH":
                require(
                    action["token"] == token and action["path"] == row["request"]["path"],
                    "RESPONSE_EVIDENCE_FINISH_INVALID",
                )
                if action["sent"]:
                    validate_response(row["response"])
                    require(
                        row["response_state"] == "OBSERVED"
                        and row["response"]["status"] == action["status"],
                        "RESPONSE_EVIDENCE_FINISH_INVALID",
                    )
                else:
                    require(
                        row["response"] is None
                        and row["response_state"] == "UNSENT"
                        and action["status"] is None,
                        "RESPONSE_EVIDENCE_UNSENT_INVALID",
                    )
                del pending[token]
            elif kind == "STOP":
                require(
                    action["token"] is None and row["response_state"] == "OUTCOME_UNCERTAIN",
                    "RESPONSE_EVIDENCE_STOP_INVALID",
                )
                if row["response"] is not None:
                    validate_response(row["response"])
            else:
                require(False, "RESPONSE_EVIDENCE_ACTION_UNSUPPORTED")

    def append(
        self, cp: CheckpointContext, directory: Path, account: str, capture: Capture
    ) -> None:
        old = (
            cp.connection.execute(
                select(read_events)
                .where(read_events.c.scope == cp.scope)
                .order_by(read_events.c.sequence)
            )
            .mappings()
            .all()
        )
        require(bool(old), "RESPONSE_EVIDENCE_AUDIT_EVENT_REQUIRED")
        binding, rows, pending, witness = self.verify(cp, directory, account, list(old[:-1]))
        audit = old[-1]
        action = audit["action"]
        kind = action["kind"]
        require(
            len(rows) < MAX_RECORDS and (kind != "RESERVE" or len(rows) + 2 <= MAX_RECORDS),
            "RESPONSE_EVIDENCE_CAPACITY_EXHAUSTED",
        )
        require(audit["revision"] == cp.revision + 1, "RESPONSE_EVIDENCE_REVISION_INVALID")
        checkpoint = cp.connection.execute(
            select(checkpoints.c.digest).where(
                checkpoints.c.scope == cp.scope, checkpoints.c.revision == audit["revision"]
            )
        ).scalar_one()
        require(checkpoint == audit["checkpoint_digest"], "RESPONSE_EVIDENCE_CHECKPOINT_INVALID")
        if kind == "RESERVE":
            require(
                capture.token is None and not capture.wire_started and capture.response is None,
                "RESPONSE_EVIDENCE_CONTEXT_INVALID",
            )
            capture.token = action["token"]
        require(capture.token is not None, "RESPONSE_EVIDENCE_CONTEXT_INVALID")
        state = (
            "PENDING"
            if kind == "RESERVE"
            else "OUTCOME_UNCERTAIN"
            if kind == "STOP"
            else "OBSERVED"
            if action["sent"]
            else "UNSENT"
        )
        require(
            kind != "FINISH" or capture.wire_started is action["sent"],
            "RESPONSE_EVIDENCE_SEND_CONTEXT_INVALID",
        )
        row = dict(
            scope=cp.scope,
            sequence=audit["sequence"],
            audit_hash=audit["hash"],
            revision=audit["revision"],
            checkpoint_digest=checkpoint,
            phase=kind,
            request_token=capture.token,
            request=capture.request,
            response=capture.response,
            response_state=state,
            previous_hash=rows[-1]["hash"] if rows else binding["root_hash"],
        )
        self._transition(row, action, pending)
        row["hash"] = digest(row)
        cp.connection.execute(insert(events).values(**row))
        _append_line(
            self.witness(directory, cp.scope),
            witness,
            _line(dict(sequence=row["sequence"], hash=row["hash"])),
        )

    @contextmanager
    def raw(self, directory: Path, scope: str, journal: UUID) -> Iterator[Any]:
        import fcntl

        obj = object.__new__(SharedWeightJournal)
        obj.directory = directory
        obj.scope = scope
        obj._paths()
        dbid, lockid = obj._identity(obj.path), obj._identity(obj.lock_path)
        with self.anchors.scope(scope, journal, read_only=True) as cp:
            assert isinstance(cp, CheckpointContext)
            fd = os.open(obj.lock_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
            local = None
            try:
                require(
                    (os.fstat(fd).st_dev, os.fstat(fd).st_ino) == lockid,
                    "RESPONSE_EVIDENCE_LOCK_REPLACED",
                )
                try:
                    fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                except BlockingIOError:
                    require(False, "RESPONSE_EVIDENCE_WRITER_BUSY")
                require(
                    obj._identity(obj.path) == dbid and obj._identity(obj.lock_path) == lockid,
                    "RESPONSE_EVIDENCE_FILE_REPLACED",
                )
                require(
                    not any(Path(str(obj.path) + s).exists() for s in ("-wal", "-shm", "-journal")),
                    "RESPONSE_EVIDENCE_SQLITE_BUSY",
                )
                local = sqlite3.connect(
                    obj.path.resolve().as_uri() + "?mode=ro",
                    uri=True,
                    isolation_level=None,
                    timeout=2,
                )
                local.row_factory = sqlite3.Row
                local.execute("PRAGMA query_only=ON")
                local.execute("BEGIN")
                yield cp, local
            finally:
                if local is not None:
                    local.close()
                os.close(fd)

    def review(
        self,
        directory: Path,
        scope: str,
        journal: UUID,
        account: str,
        *,
        sequence: int | None = None,
        body: bytes | None = None,
    ) -> dict[str, Any]:
        with self.raw(directory, scope, journal) as (cp, local):
            old, state = self.audit._verified_history(cp, local)
            binding, rows, pending, _ = self.verify(cp, directory, account, old)
            matches = False
            if sequence is not None:
                selected = [r for r in rows if r["sequence"] == sequence]
                require(
                    len(selected) == 1
                    and selected[0]["phase"] == "FINISH"
                    and selected[0]["response_state"] == "OBSERVED"
                    and isinstance(body, bytes)
                    and len(body) <= MAX_BODY,
                    "RESPONSE_EVIDENCE_BODY_REQUIRED",
                )
                assert isinstance(body, bytes)
                meta = selected[0]["response"]
                require(
                    len(body) == meta["body_bytes"]
                    and hashlib.sha256(body).hexdigest() == meta["body_sha256"],
                    "RESPONSE_EVIDENCE_BODY_MISMATCH",
                )
                matches = True
            return dict(
                response_evidence_review="INTEGRITY_VERIFIED_KEEP_PAUSED",
                base_audit_sequence=binding["base_sequence"],
                evidence_events=len(rows),
                pending_requests=len(pending),
                shared_stopped=bool(state[1][0][8]),
                completed_observed_reads=sum(
                    r["phase"] == "FINISH" and r["response_state"] == "OBSERVED" for r in rows
                ),
                response_body_digest_matches=matches,
                response_body_artifact_stored=False,
                request_signature_or_key_stored=False,
                remote_account_identity_verified=False,
                archive_authorized=False,
                financial_proof_verified=False,
                order_writes=0,
                execution_approved=False,
                orders_enabled=False,
                live_trading_enabled=False,
            )


class EvidenceReadJournal(AuditedDemoReadJournal):
    def __init__(
        self,
        *args: Any,
        evidence: ResponseEvidenceStore,
        account: str,
        enable_evidence_binding: bool = False,
        **kwargs: Any,
    ):
        require(enable_evidence_binding is True, "RESPONSE_EVIDENCE_DISABLED")
        self.evidence = evidence
        self.account = account
        self._configured_account = account
        self._configured_evidence = evidence
        self.capture: ContextVar[Capture | None] = ContextVar(
            "response_evidence_capture", default=None
        )
        kwargs.setdefault("read_only", True)
        require(
            kwargs.get("audit") is not None and kwargs["audit"].engine is evidence.engine,
            "RESPONSE_EVIDENCE_ENGINE_MISMATCH",
        )
        super().__init__(*args, **kwargs)

    def _verify_checkpoint(self, c: sqlite3.Connection) -> None:
        require(
            self.account == self._configured_account and self.evidence is self._configured_evidence,
            "RESPONSE_EVIDENCE_PROVIDER_CHANGED",
        )
        super()._verify_checkpoint(c)
        cp = self.checkpoint.get()
        assert cp is not None
        old, _ = self.audit._verified_history(cp, c)
        self.evidence.verify(cp, self.directory, self.account, old)
        meta = c.execute("SELECT * FROM meta").fetchone()
        verify_clock(meta, self.monotonic(), self.wall_clock(), self.boot())

    def _stage_checkpoint(self, c: sqlite3.Connection) -> None:
        capture = self.capture.get()
        require(capture is not None, "RESPONSE_EVIDENCE_CAPTURE_REQUIRED")
        assert capture is not None
        super()._stage_checkpoint(c)
        cp = self.checkpoint.get()
        assert cp is not None
        self.evidence.append(cp, self.directory, self.account, capture)
