"""Read-only, as-of audited observations; never reconciliation or archive authority.

Caller expectations are not an authenticated local submission or remote-account
binding. Exact-byte integrity and identity/quantity checks do not remove that gap.
"""

import copy
import hashlib
import json
import os
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from packages.persistence.demo_order_quota import LocalQuotaStopped
from packages.persistence.demo_read_audit import digest, require
from packages.persistence.demo_read_recovery import (
    _file,
    _private_directory,
    _sync_directory,
    _write_new,
)
from packages.persistence.demo_response_evidence import MAX_BODY, ResponseEvidenceStore
from packages.persistence.demo_shared_weight import SharedWeightJournal
from services.execution.demo_query_contract import identifier, integer

POLICY = "AUDITED_REQUEST_OUTCOME_OBSERVATION_CANDIDATE_V1"
TERMINAL = frozenset({"FILLED", "CANCELED", "REJECTED", "EXPIRED", "EXPIRED_IN_MATCH"})


def quantity(value: Any) -> Decimal:
    require(
        isinstance(value, str)
        and re.fullmatch(r"[0-9]{1,16}(?:\.[0-9]{1,16})?", value) is not None,
        "REQUEST_OUTCOME_QUANTITY_INVALID",
    )
    return Decimal(value)


@dataclass(frozen=True)
class RequestExpectation:
    role: Literal["ENTRY", "EXIT", "STOP_LOSS"]
    client_id: str
    side: Literal["BUY", "SELL"]
    order_type: str
    quantity: str | None
    venue_order_id: str | None = None

    def __post_init__(self) -> None:
        require(
            self.role in {"ENTRY", "EXIT", "STOP_LOSS"}
            and isinstance(self.client_id, str)
            and identifier(self.client_id)
            and self.side in {"BUY", "SELL"}
            and (
                self.venue_order_id is None
                or isinstance(self.venue_order_id, str)
                and integer(self.venue_order_id, positive=True)
            ),
            "REQUEST_OUTCOME_EXPECTATION_INVALID",
        )
        if self.role == "STOP_LOSS":
            require(
                self.order_type == "STOP_MARKET" and self.quantity is None,
                "REQUEST_OUTCOME_ALGO_EXPECTATION_INVALID",
            )
        else:
            require(
                self.order_type in ({"LIMIT"} if self.role == "ENTRY" else {"LIMIT", "MARKET"})
                and quantity(self.quantity) > 0,
                "REQUEST_OUTCOME_REGULAR_EXPECTATION_INVALID",
            )


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        require(key not in result, "REQUEST_OUTCOME_JSON_DUPLICATE_KEY")
        result[key] = value
    return result


def _constant(value: str) -> None:
    require(False, "REQUEST_OUTCOME_JSON_NONFINITE")


def _json(body: bytes) -> dict[str, Any]:
    try:
        value = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_constant=_constant,
            parse_float=Decimal,
        )
    except (ValueError, UnicodeError, RecursionError):
        raise LocalQuotaStopped("REQUEST_OUTCOME_JSON_INVALID") from None
    require(isinstance(value, dict), "REQUEST_OUTCOME_JSON_OBJECT_REQUIRED")
    assert isinstance(value, dict)
    return value


def _id(value: Any) -> str:
    require(type(value) in {str, int}, "REQUEST_OUTCOME_VENUE_ID_INVALID")
    result = str(value)
    require(integer(result, positive=True), "REQUEST_OUTCOME_VENUE_ID_INVALID")
    return result


def _observation(
    store: ResponseEvidenceStore,
    directory: Path,
    scope: str,
    journal: UUID,
    account: str,
    sequence: int,
    body: bytes,
) -> dict[str, Any]:
    require(
        type(sequence) is int
        and sequence >= 0
        and isinstance(body, bytes)
        and len(body) <= MAX_BODY,
        "REQUEST_OUTCOME_OBSERVATION_ARGUMENT_INVALID",
    )
    with store.raw(directory, scope, journal) as (cp, local):
        old, _ = store.audit._verified_history(cp, local)
        binding, rows, _, _ = store.verify(cp, directory, account, old)
        selected = [r for r in rows if r["sequence"] == sequence]
        require(
            len(selected) == 1
            and selected[0]["phase"] == "FINISH"
            and selected[0]["response_state"] == "OBSERVED",
            "REQUEST_OUTCOME_OBSERVED_FINISH_REQUIRED",
        )
        row = selected[0]
        require(
            row["response"]["body_bytes"] == len(body)
            and row["response"]["body_sha256"] == hashlib.sha256(body).hexdigest(),
            "REQUEST_OUTCOME_BODY_MISMATCH",
        )
        return copy.deepcopy(dict(row)) | {"binding_root_hash": binding["root_hash"]}


def _classify(row: dict[str, Any], body: bytes, expected: RequestExpectation) -> dict[str, Any]:
    req, meta = row["request"], row["response"]
    path = "/fapi/v1/algoOrder" if expected.role == "STOP_LOSS" else "/fapi/v1/order"
    require(
        req["method"] == "GET" and req["authenticated"] is True and req["path"] == path,
        "REQUEST_OUTCOME_ROUTE_ROLE_MISMATCH",
    )
    params = req["params"]
    if expected.role == "STOP_LOSS":
        require(params == {"clientAlgoId": expected.client_id}, "REQUEST_OUTCOME_QUERY_ID_MISMATCH")
    else:
        require(
            params == {"symbol": "BTCUSDT", "origClientOrderId": expected.client_id}
            or expected.venue_order_id is not None
            and params == {"symbol": "BTCUSDT", "orderId": expected.venue_order_id},
            "REQUEST_OUTCOME_QUERY_ID_MISMATCH",
        )
    value = _json(body)
    if meta["status"] != 200:
        code = value.get("code")
        require(type(code) is int, "REQUEST_OUTCOME_HTTP_ERROR_SHAPE_INVALID")
        return dict(
            observation="NOT_FOUND_UNRESOLVED"
            if code in {-2011, -2013}
            else "HTTP_ERROR_UNRESOLVED",
            response_identity_matches=False,
            terminal_order_observed=False,
            underlying_execution_proof_required=expected.role == "STOP_LOSS",
        )
    require(
        value.get("symbol") == "BTCUSDT"
        and value.get("side") == expected.side
        and value.get("positionSide") == "BOTH",
        "REQUEST_OUTCOME_RESPONSE_IDENTITY_MISMATCH",
    )
    if expected.role == "STOP_LOSS":
        require(
            value.get("clientAlgoId") == expected.client_id
            and value.get("orderType") == expected.order_type
            and value.get("closePosition") is True,
            "REQUEST_OUTCOME_ALGO_IDENTITY_MISMATCH",
        )
        venue_id = _id(value.get("algoId"))
        require(
            expected.venue_order_id is None or expected.venue_order_id == venue_id,
            "REQUEST_OUTCOME_RESPONSE_VENUE_ID_MISMATCH",
        )
        status = value.get("algoStatus")
        require(
            isinstance(status, str)
            and status in {"NEW", "CANCELED", "EXPIRED", "REJECTED", "TRIGGERED", "FINISHED"},
            "REQUEST_OUTCOME_ALGO_STATUS_UNSUPPORTED",
        )
        return dict(
            observation="ALGO_STATUS_OBSERVED",
            observed_status=status,
            response_identity_matches=True,
            terminal_order_observed=False,
            underlying_execution_proof_required=True,
        )
    require(
        value.get("clientOrderId") == expected.client_id
        and value.get("type") == expected.order_type
        and value.get("reduceOnly") is (expected.role == "EXIT"),
        "REQUEST_OUTCOME_REGULAR_IDENTITY_MISMATCH",
    )
    venue_id = _id(value.get("orderId"))
    require(
        expected.venue_order_id is None or expected.venue_order_id == venue_id,
        "REQUEST_OUTCOME_RESPONSE_VENUE_ID_MISMATCH",
    )
    original, filled = quantity(value.get("origQty")), quantity(value.get("executedQty"))
    require(
        original == quantity(expected.quantity) and 0 <= filled <= original,
        "REQUEST_OUTCOME_QUANTITY_MISMATCH",
    )
    status = value.get("status")
    require(
        isinstance(status, str) and status in TERMINAL | {"NEW", "PARTIALLY_FILLED"},
        "REQUEST_OUTCOME_STATUS_UNSUPPORTED",
    )
    require(
        (status != "FILLED" or filled == original)
        and (status not in {"NEW", "REJECTED"} or filled == 0)
        and (status != "PARTIALLY_FILLED" or 0 < filled < original),
        "REQUEST_OUTCOME_STATUS_QUANTITY_CONFLICT",
    )
    return dict(
        observation="TERMINAL_REGULAR_ORDER_OBSERVED"
        if status in TERMINAL
        else "REGULAR_ORDER_STILL_ACTIVE",
        observed_status=status,
        response_identity_matches=True,
        terminal_order_observed=status in TERMINAL,
        original_quantity=str(original),
        executed_quantity=str(filled),
        fills_or_exposure_followup_required=filled > 0,
        underlying_execution_proof_required=False,
    )


def review_request_outcome(
    store: ResponseEvidenceStore,
    directory: Path,
    scope: str,
    journal: UUID,
    account: str,
    sequence: int,
    body: bytes,
    expected: RequestExpectation,
) -> dict[str, Any]:
    require(type(expected) is RequestExpectation, "REQUEST_OUTCOME_EXPECTATION_REQUIRED")
    row = _observation(store, directory, scope, journal, account, sequence, body)
    finding = _classify(row, body, expected)
    return dict(
        request_outcome_review="AUDITED_AS_OF_OBSERVATION_KEEP_PAUSED",
        policy=POLICY,
        role=expected.role,
        evidence_sequence=sequence,
        evidence_hash=row["hash"],
        binding_root_hash=row["binding_root_hash"],
        response_body_sha256=row["response"]["body_sha256"],
        request_timestamp_ms=row["request"]["timestamp"],
        http_status=row["response"]["status"],
        response_body_digest_matches=True,
        expectation_source="CALLER_SUPPLIED_NOT_SUBMISSION_BOUND",
        local_submission_binding_verified=False,
        remote_account_identity_verified=False,
        fresh_exchange_state_verified=False,
        remote_request_resolution_verified=False,
        position_closed_verified=False,
        no_send_verified=False,
        archive_authorized=False,
        reconciliation_performed=False,
        database_changed=False,
        filesystem_changed=False,
        http_attempts=0,
        order_writes=0,
        execution_approved=False,
        orders_enabled=False,
        live_trading_enabled=False,
        **finding,
    )


def preserve_response_artifact(
    artifact_directory: Path,
    store: ResponseEvidenceStore,
    directory: Path,
    scope: str,
    journal: UUID,
    account: str,
    sequence: int,
    body: bytes,
    expected: RequestExpectation,
    *,
    apply: bool = False,
) -> dict[str, Any]:
    """Explicit immutable private import, never a response-body backfill into V1."""
    require(apply is True, "REQUEST_OUTCOME_EXPLICIT_ARTIFACT_IMPORT_REQUIRED")
    report = review_request_outcome(
        store, directory, scope, journal, account, sequence, body, expected
    )
    _private_directory(artifact_directory)
    require(
        artifact_directory.resolve() != directory.resolve()
        and directory.resolve() not in artifact_directory.resolve().parents,
        "REQUEST_OUTCOME_SEPARATE_ARTIFACT_DIRECTORY_REQUIRED",
    )
    reference = digest(
        dict(
            scope=scope,
            journal=str(journal),
            account=account,
            evidence_hash=report["evidence_hash"],
            policy=POLICY,
        )
    )
    path = artifact_directory / (reference + ".response-body")
    if path.exists() or path.is_symlink():
        require(_file(path, MAX_BODY) == body, "REQUEST_OUTCOME_EXISTING_ARTIFACT_MISMATCH")
        created = False
    else:
        _write_new(path, body)
        created = True
    identity = SharedWeightJournal._identity(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        require((info.st_dev, info.st_ino) == identity, "REQUEST_OUTCOME_ARTIFACT_REPLACED")
        os.fsync(fd)
    finally:
        os.close(fd)
    _sync_directory(artifact_directory)
    require(_file(path, MAX_BODY) == body, "REQUEST_OUTCOME_ARTIFACT_WRITE_MISMATCH")
    return report | dict(
        response_body_artifact_stored=True,
        artifact_created=created,
        artifact_reference=reference,
        filesystem_changed=created,
    )


def review_response_artifact(
    artifact_directory: Path,
    artifact_reference: str,
    store: ResponseEvidenceStore,
    directory: Path,
    scope: str,
    journal: UUID,
    account: str,
    sequence: int,
    expected: RequestExpectation,
) -> dict[str, Any]:
    require(
        isinstance(artifact_reference, str)
        and re.fullmatch("[0-9a-f]{64}", artifact_reference) is not None,
        "REQUEST_OUTCOME_ARTIFACT_REFERENCE_INVALID",
    )
    _private_directory(artifact_directory)
    body = _file(artifact_directory / (artifact_reference + ".response-body"), MAX_BODY)
    report = review_request_outcome(
        store, directory, scope, journal, account, sequence, body, expected
    )
    require(
        artifact_reference
        == digest(
            dict(
                scope=scope,
                journal=str(journal),
                account=account,
                evidence_hash=report["evidence_hash"],
                policy=POLICY,
            )
        ),
        "REQUEST_OUTCOME_ARTIFACT_BINDING_MISMATCH",
    )
    return report | dict(response_body_artifact_stored=True, artifact_reference=artifact_reference)
