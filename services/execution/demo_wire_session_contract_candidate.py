"""Synthetic-only wire boundary contract; no network or production integration.

Secrets remain in process RAM. Local session continuity is not remote account
identity. The private file chain has no external rollback anchor or PG coupling.
"""

import hashlib
import hmac
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl
from uuid import UUID, uuid4

from pydantic import SecretStr

from integrations.binance_usdm.rest import RESTResponse, sign_payload
from packages.persistence.demo_order_quota import LocalQuotaStopped, canonical
from packages.persistence.demo_read_audit import digest, require
from packages.persistence.demo_read_recovery import (
    _file,
    _private_directory,
    _sync_directory,
    _write_new,
)
from packages.persistence.demo_response_evidence import (
    _append_line,
    _line,
    response_metadata,
    validate_response,
)
from services.execution.demo_query_contract import SIGNING, require_signed_query, require_wire_query
from services.execution.demo_submission_link_candidate import IntentPin

POLICY = "SYNTHETIC_WIRE_SESSION_CONTRACT_CANDIDATE_V1"
MAX_RECORDS = 256
MAX_BYTES = 524288
MAX_SESSION_SECONDS = 12 * 60 * 60
GETS = {"/fapi/v1/order", "/fapi/v1/algoOrder", "/fapi/v1/accountConfig", "/fapi/v3/account"}


def _uuid(value: Any) -> None:
    require(isinstance(value, str), "WIRE_CONTRACT_UUID_INVALID")
    try:
        require(str(UUID(value)) == value, "WIRE_CONTRACT_UUID_INVALID")
    except (ValueError, AttributeError):
        raise LocalQuotaStopped("WIRE_CONTRACT_UUID_INVALID") from None


def _hash(value: Any) -> None:
    require(
        isinstance(value, str) and re.fullmatch("[0-9a-f]{64}", value) is not None,
        "WIRE_CONTRACT_DIGEST_INVALID",
    )


def _scope(account: Any, scope: Any) -> None:
    require(
        all(
            isinstance(v, str) and re.fullmatch(r"DEMO-[A-Za-z0-9_-]{1,74}", v) is not None
            for v in (account, scope)
        ),
        "WIRE_CONTRACT_SCOPE_INVALID",
    )


def _role(method: str, path: str, params: dict[str, str]) -> str:
    require_signed_query(method, path, params)
    if method == "GET":
        require(path in GETS, "WIRE_CONTRACT_ROUTE_UNSUPPORTED")
        return "READ"
    require(method == "POST", "WIRE_CONTRACT_MUTATION_UNSUPPORTED")
    if path == "/fapi/v1/order":
        return "EXIT" if params.get("reduceOnly") == "true" else "ENTRY"
    require(
        path == "/fapi/v1/algoOrder" and params.get("type") == "STOP_MARKET",
        "WIRE_CONTRACT_ALGO_UNSUPPORTED",
    )
    return "STOP_LOSS"


def _validate_envelope(value: Any) -> None:
    require(
        isinstance(value, dict)
        and set(value)
        == {
            "operation",
            "session",
            "method",
            "path",
            "params",
            "timestamp",
            "recvWindow",
            "role",
            "unsigned_query_sha256",
            "intent_reference",
            "local_signature_checked",
        },
        "WIRE_CONTRACT_ENVELOPE_INVALID",
    )
    _uuid(value["operation"])
    _uuid(value["session"])
    _hash(value["unsigned_query_sha256"])
    require(
        value["local_signature_checked"] is True
        and value["recvWindow"] == "5000"
        and isinstance(value["timestamp"], str)
        and re.fullmatch(r"[0-9]{1,20}", value["timestamp"]) is not None
        and isinstance(value["method"], str)
        and isinstance(value["path"], str)
        and isinstance(value["params"], dict),
        "WIRE_CONTRACT_ENVELOPE_INVALID",
    )
    require(
        value["role"] == _role(value["method"], value["path"], value["params"]),
        "WIRE_CONTRACT_ROLE_MISMATCH",
    )
    pin = value["intent_reference"]
    if value["role"] == "READ":
        require(pin is None, "WIRE_CONTRACT_READ_INTENT_INVALID")
    else:
        require(
            isinstance(pin, dict) and set(pin) == {"event_id", "envelope_sha256"},
            "WIRE_CONTRACT_INTENT_REQUIRED",
        )
        _uuid(pin["event_id"])
        _hash(pin["envelope_sha256"])


@dataclass(frozen=True)
class PreparedWire:
    operation: UUID
    _serialized: str = field(repr=False)
    _query: SecretStr = field(repr=False)
    _key: SecretStr = field(repr=False)

    def __reduce_ex__(self, protocol: Any) -> Any:
        raise LocalQuotaStopped("WIRE_CONTRACT_PREPARED_NOT_SERIALIZABLE")

    def __copy__(self) -> "PreparedWire":
        return PreparedWire(self.operation, self._serialized, self._query, self._key)

    def __deepcopy__(self, memo: Any) -> Any:
        raise LocalQuotaStopped("WIRE_CONTRACT_PREPARED_NOT_SERIALIZABLE")

    def record(self) -> dict[str, Any]:
        result: dict[str, Any] = json.loads(self._serialized)
        return result


class CredentialSession:
    """RAM-only capability. A new instance always gets a new session UUID."""

    __slots__ = (
        "_account",
        "_scope",
        "_id",
        "_pid",
        "_key",
        "_secret",
        "_closed",
        "_created",
        "_captures",
        "_lock",
    )

    def __init__(
        self,
        account: str,
        scope: str,
        key: SecretStr,
        secret: SecretStr,
        *,
        enable_synthetic_contract: bool = False,
    ):
        require(enable_synthetic_contract is True, "WIRE_CONTRACT_SESSION_DISABLED")
        _scope(account, scope)
        require(
            all(
                isinstance(v, SecretStr)
                and 1 <= len(v.get_secret_value()) <= 256
                and v.get_secret_value().isascii()
                for v in (key, secret)
            ),
            "WIRE_CONTRACT_CREDENTIALS_INVALID",
        )
        self._account, self._scope, self._id, self._pid = account, scope, str(uuid4()), os.getpid()
        self._key, self._secret = (
            SecretStr(key.get_secret_value()),
            SecretStr(secret.get_secret_value()),
        )
        self._closed = False
        self._created = time.monotonic()
        self._captures: dict[UUID, PreparedWire] = {}
        self._lock = threading.RLock()

    def __repr__(self) -> str:
        return "CredentialSession(<private synthetic process-local capability>)"

    def __reduce_ex__(self, protocol: Any) -> Any:
        raise LocalQuotaStopped("WIRE_CONTRACT_SESSION_NOT_SERIALIZABLE")

    def __copy__(self) -> Any:
        raise LocalQuotaStopped("WIRE_CONTRACT_SESSION_NOT_COPYABLE")

    def __deepcopy__(self, memo: Any) -> Any:
        raise LocalQuotaStopped("WIRE_CONTRACT_SESSION_NOT_COPYABLE")

    def _check(self) -> None:
        elapsed = time.monotonic() - self._created
        require(
            not self._closed and self._pid == os.getpid() and 0 <= elapsed < MAX_SESSION_SECONDS,
            "WIRE_CONTRACT_SESSION_CLOSED_FORKED_OR_EXPIRED",
        )

    def descriptor(self) -> dict[str, str]:
        with self._lock:
            self._check()
            return dict(
                session=self._id,
                account=self._account,
                scope=self._scope,
                kind="PROCESS_LOCAL_SYNTHETIC_CREDENTIALS",
            )

    def _wire(
        self, method: str, path: str, query: SecretStr, key: SecretStr
    ) -> tuple[dict[str, str], str]:
        self._check()
        require(
            isinstance(query, SecretStr) and isinstance(key, SecretStr),
            "WIRE_CONTRACT_WIRE_INVALID",
        )
        require_wire_query(method, path, query, key)
        raw = query.get_secret_value()
        require("&signature=" in raw, "WIRE_CONTRACT_SIGNATURE_POSITION_INVALID")
        unsigned, signature = raw.rsplit("&signature=", 1)
        require(
            key.get_secret_value().isascii()
            and hmac.compare_digest(key.get_secret_value(), self._key.get_secret_value())
            and hmac.compare_digest(sign_payload(self._secret, unsigned), signature),
            "WIRE_CONTRACT_SESSION_SIGNATURE_MISMATCH",
        )
        values = dict(parse_qsl(raw, strict_parsing=True, max_num_fields=32))
        _role(method, path, {k: v for k, v in values.items() if k not in SIGNING})
        return values, hashlib.sha256(unsigned.encode()).hexdigest()

    def prepare(
        self,
        method: str,
        path: str,
        query: SecretStr,
        key: SecretStr,
        *,
        intent: IntentPin | None = None,
    ) -> PreparedWire:
        with self._lock:
            values, wire_hash = self._wire(method, path, query, key)
            params = {k: v for k, v in values.items() if k not in SIGNING}
            role = _role(method, path, params)
            require(
                (role == "READ" and intent is None)
                or (role != "READ" and type(intent) is IntentPin),
                "WIRE_CONTRACT_INTENT_REQUIRED",
            )
            require(len(self._captures) < 64, "WIRE_CONTRACT_PENDING_CAPTURE_LIMIT")
            operation = uuid4()
            value = dict(
                operation=str(operation),
                session=self._id,
                method=method,
                path=path,
                params=params,
                timestamp=values["timestamp"],
                recvWindow=values["recvWindow"],
                role=role,
                unsigned_query_sha256=wire_hash,
                local_signature_checked=True,
                intent_reference=None
                if intent is None
                else dict(event_id=str(intent.event_id), envelope_sha256=intent.envelope_sha256),
            )
            _validate_envelope(value)
            prepared = PreparedWire(operation, canonical(value), query, key)
            self._captures[operation] = prepared
            return prepared

    def _verify(self, prepared: PreparedWire) -> dict[str, Any]:
        self._check()
        value = prepared.record()
        require(value["session"] == self._id, "WIRE_CONTRACT_SESSION_MISMATCH")
        values, wire_hash = self._wire(
            value["method"], value["path"], prepared._query, prepared._key
        )
        require(
            wire_hash == value["unsigned_query_sha256"]
            and values["timestamp"] == value["timestamp"]
            and {k: v for k, v in values.items() if k not in SIGNING} == value["params"],
            "WIRE_CONTRACT_PREPARED_WIRE_CHANGED",
        )
        return value

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._captures.clear()
            self._key = self._secret = SecretStr("")


class SyntheticResponder:
    """Sealed data-only test double; cannot accept a callable/network transport."""

    __slots__ = ("_response", "_fail", "calls")

    def __init__(self, response: RESTResponse, *, fail: bool = False):
        require(
            type(response) is RESTResponse and type(fail) is bool,
            "WIRE_CONTRACT_SYNTHETIC_RESPONSE_REQUIRED",
        )
        self._response, self._fail, self.calls = response, fail, 0

    def _respond(self) -> RESTResponse:
        self.calls += 1
        if self._fail:
            raise OSError("SYNTHETIC_WIRE_OUTCOME_UNCERTAIN")
        return self._response


def _records(raw: bytes) -> list[dict[str, Any]]:
    from services.execution.demo_request_outcome_candidate import _pairs

    require(len(raw) <= MAX_BYTES and raw.endswith(b"\n"), "WIRE_CONTRACT_LOG_INVALID")
    try:
        result = [json.loads(line, object_pairs_hook=_pairs) for line in raw.splitlines()]
    except (ValueError, UnicodeError, RecursionError):
        raise LocalQuotaStopped("WIRE_CONTRACT_LOG_INVALID") from None
    require(1 <= len(result) <= MAX_RECORDS, "WIRE_CONTRACT_RECORD_LIMIT")
    previous = "0" * 64
    for index, row in enumerate(result):
        require(
            isinstance(row, dict)
            and set(row) == {"sequence", "previous_hash", "kind", "data", "hash"}
            and isinstance(row["kind"], str)
            and type(row["sequence"]) is int
            and row["sequence"] == index
            and row["previous_hash"] == previous
            and digest({k: v for k, v in row.items() if k != "hash"}) == row["hash"],
            "WIRE_CONTRACT_CHAIN_INVALID",
        )
        previous = row["hash"]
    return result


def _replay(rows: list[dict[str, Any]]) -> dict[str, Any]:
    root = rows[0]
    require(
        root["kind"] == "ROOT"
        and isinstance(root["data"], dict)
        and set(root["data"]) == {"policy", "environment", "journal", "account", "scope"}
        and root["data"]["policy"] == POLICY
        and root["data"]["environment"] == "SYNTHETIC_ONLY",
        "WIRE_CONTRACT_ROOT_INVALID",
    )
    _scope(root["data"]["account"], root["data"]["scope"])
    _uuid(root["data"]["journal"])
    sessions: set[str] = set()
    operations: dict[str, Any] = {}
    posts: set[tuple[str, str]] = set()
    unknown = False
    for row in rows[1:]:
        kind, data = row["kind"], row["data"]
        require(not unknown and isinstance(data, dict), "WIRE_CONTRACT_AFTER_UNCERTAIN_OUTCOME")
        if kind == "SESSION":
            require(
                set(data) == {"session", "account", "scope", "kind"}
                and data["account"] == root["data"]["account"]
                and data["scope"] == root["data"]["scope"]
                and data["kind"] == "PROCESS_LOCAL_SYNTHETIC_CREDENTIALS",
                "WIRE_CONTRACT_SESSION_DESCRIPTOR_INVALID",
            )
            _uuid(data["session"])
            require(data["session"] not in sessions, "WIRE_CONTRACT_SESSION_DUPLICATE")
            sessions.add(data["session"])
        elif kind == "PREPARE":
            _validate_envelope(data)
            require(
                data["session"] in sessions and data["operation"] not in operations,
                "WIRE_CONTRACT_PREPARE_INVALID",
            )
            if data["method"] == "POST":
                key = (
                    data["path"],
                    data["params"].get("newClientOrderId", data["params"].get("clientAlgoId", "")),
                )
                require(key not in posts, "WIRE_CONTRACT_DUPLICATE_MUTATION_ID")
                posts.add(key)
            operations[data["operation"]] = dict(phase=kind, envelope=data)
        else:
            require(
                kind in {"DISPATCH_BEGIN", "OBSERVED", "UNKNOWN"}, "WIRE_CONTRACT_PHASE_INVALID"
            )
            allowed = {"operation", "response"} if kind == "OBSERVED" else {"operation"}
            require(set(data) == allowed, "WIRE_CONTRACT_OPERATION_INVALID")
            _uuid(data["operation"])
            require(data["operation"] in operations, "WIRE_CONTRACT_OPERATION_INVALID")
            op = operations[data["operation"]]
            require(
                op["phase"] == ("PREPARE" if kind == "DISPATCH_BEGIN" else "DISPATCH_BEGIN"),
                "WIRE_CONTRACT_PHASE_INVALID",
            )
            if kind == "OBSERVED":
                validate_response(data["response"])
            op["phase"] = kind
            unknown = kind == "UNKNOWN"
    return dict(sessions=sessions, operations=operations, posts=posts, unknown=unknown)


class SyntheticWireJournal:
    """One writer lifetime only. Reopening a log never enables sending/resumption."""

    directory: Path
    path: Path
    _pid: int
    _poisoned: bool
    _lock: Any
    _raw: bytes
    _rows: list[dict[str, Any]]

    @classmethod
    def create(
        cls, directory: Path, account: str, scope: str, *, enable_synthetic_contract: bool = False
    ) -> "SyntheticWireJournal":
        require(enable_synthetic_contract is True, "WIRE_CONTRACT_JOURNAL_DISABLED")
        _scope(account, scope)
        _private_directory(directory)
        obj = cls()
        obj.directory, obj.path = directory, directory / "wire-contract-v1.log"
        obj._pid, obj._poisoned, obj._lock = os.getpid(), False, threading.RLock()
        obj._raw = b""
        obj._rows = []
        data = dict(
            policy=POLICY,
            environment="SYNTHETIC_ONLY",
            journal=str(uuid4()),
            account=account,
            scope=scope,
        )
        row = dict(sequence=0, previous_hash="0" * 64, kind="ROOT", data=data)
        row["hash"] = digest(row)
        obj._raw = _line(row)
        _write_new(obj.path, obj._raw)
        _sync_directory(directory)
        obj._rows = [row]
        return obj

    def _append(self, kind: str, data: dict[str, Any]) -> None:
        require(not self._poisoned and self._pid == os.getpid(), "WIRE_CONTRACT_WRITER_BLOCKED")
        try:
            row = dict(
                sequence=len(self._rows), previous_hash=self._rows[-1]["hash"], kind=kind, data=data
            )
            row["hash"] = digest(row)
            candidate = self._rows + [row]
            _replay(candidate)
            line = _line(row)
            require(
                len(candidate) <= MAX_RECORDS and len(self._raw) + len(line) <= MAX_BYTES,
                "WIRE_CONTRACT_RECORD_LIMIT",
            )
            _append_line(self.path, self._raw, line)
            self._rows, self._raw = candidate, self._raw + line
        except Exception:
            self._poisoned = True
            raise LocalQuotaStopped("WIRE_CONTRACT_APPEND_FAILED_NO_RETRY") from None

    def exercise(
        self,
        session: CredentialSession,
        prepared: PreparedWire,
        responder: SyntheticResponder,
        *,
        enable_synthetic_dispatch: bool = False,
    ) -> RESTResponse:
        require(enable_synthetic_dispatch is True, "WIRE_CONTRACT_DISPATCH_DISABLED")
        require(
            type(session) is CredentialSession
            and type(prepared) is PreparedWire
            and type(responder) is SyntheticResponder,
            "WIRE_CONTRACT_SYNTHETIC_CAPABILITY_REQUIRED",
        )
        with self._lock, session._lock:
            require(not self._poisoned and self._pid == os.getpid(), "WIRE_CONTRACT_WRITER_BLOCKED")
            try:
                require(
                    _file(self.path, MAX_BYTES) == self._raw, "WIRE_CONTRACT_WRITER_TIP_MISMATCH"
                )
            except Exception:
                self._poisoned = True
                raise LocalQuotaStopped("WIRE_CONTRACT_WRITER_TIP_MISMATCH") from None
            descriptor = session.descriptor()
            root = self._rows[0]["data"]
            require(
                descriptor["account"] == root["account"] and descriptor["scope"] == root["scope"],
                "WIRE_CONTRACT_SESSION_SCOPE_MISMATCH",
            )
            require(
                session._captures.get(prepared.operation) is prepared,
                "WIRE_CONTRACT_CAPTURE_REUSED_OR_FORGED",
            )
            envelope = session._verify(prepared)
            state = _replay(self._rows)
            if descriptor["session"] not in state["sessions"]:
                self._append("SESSION", descriptor)
            # Three records must fit before consuming admission for this simulation.
            require(
                len(self._rows) + 3 <= MAX_RECORDS and len(self._raw) + 16384 <= MAX_BYTES,
                "WIRE_CONTRACT_RECORD_LIMIT",
            )
            del session._captures[prepared.operation]
            self._append("PREPARE", envelope)
            self._append("DISPATCH_BEGIN", dict(operation=str(prepared.operation)))
            try:
                session._verify(prepared)
                response = responder._respond()
                session._verify(prepared)
                meta = response_metadata(response)
            except Exception:
                try:
                    self._append("UNKNOWN", dict(operation=str(prepared.operation)))
                finally:
                    self._poisoned = True
                raise LocalQuotaStopped("WIRE_CONTRACT_OUTCOME_UNCERTAIN_NO_RETRY") from None
            self._append("OBSERVED", dict(operation=str(prepared.operation), response=meta))
            return response


def review_synthetic_journal(directory: Path) -> dict[str, Any]:
    _private_directory(directory)
    rows = _records(_file(directory / "wire-contract-v1.log", MAX_BYTES))
    state = _replay(rows)
    pending = sum(op["phase"] != "OBSERVED" for op in state["operations"].values())
    return dict(
        wire_contract_review="SYNTHETIC_STRUCTURE_ONLY_KEEP_PAUSED",
        policy=POLICY,
        records=len(rows),
        sessions=len(state["sessions"]),
        operations=len(state["operations"]),
        pending_operations=pending,
        uncertain_outcome=state["unknown"],
        hash_chain_structure_verified=True,
        external_rollback_protection_verified=False,
        recorded_signature_checks_are_independent_proof=False,
        session_continuity_scope="SINGLE_IN_PROCESS_SYNTHETIC_OWNER_ONLY",
        process_restart_resumption_supported=False,
        original_intent_authenticated=False,
        remote_account_identity_verified=False,
        real_wire_submission_verified=False,
        database_commit_coupling_verified=False,
        mutation_authorization_verified=False,
        archive_authorized=False,
        actual_http_attempts=0,
        order_writes=0,
        execution_approved=False,
        orders_enabled=False,
        live_trading_enabled=False,
    )
