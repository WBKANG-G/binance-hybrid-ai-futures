"""Bounded read-only intent matching, never historical authenticity or account proof.

The legacy event is a mutable full-ledger snapshot, not a signed wire envelope.
A caller pin detects content changes relative to that pin; it supplies no authority.
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine

from packages.persistence.demo import events, outbox
from packages.persistence.demo_read_audit import digest, require
from packages.persistence.demo_response_evidence import ResponseEvidenceStore
from services.execution.demo_query_contract import identifier
from services.execution.demo_request_outcome_candidate import (
    RequestExpectation,
    _json,
    _observation,
    quantity,
    review_request_outcome,
)

POLICY = "PINNED_LOCAL_INTENT_OBSERVATION_LINK_CANDIDATE_V1"
KINDS = {
    "ENTRY": "DEMO_ENTRY_INTENT",
    "STOP_LOSS": "DEMO_SL_INTENT",
    "EXIT": "DEMO_REDUCE_ONLY_INTENT",
}
MAX_PAYLOAD = 1_048_576


@dataclass(frozen=True)
class IntentPin:
    event_id: UUID
    envelope_sha256: str

    def __post_init__(self) -> None:
        require(
            isinstance(self.event_id, UUID)
            and isinstance(self.envelope_sha256, str)
            and re.fullmatch("[0-9a-f]{64}", self.envelope_sha256) is not None,
            "SUBMISSION_LINK_PIN_INVALID",
        )


def _intent(
    engine: Engine, account: str, event_id: UUID, role: str, request_id: str
) -> tuple[dict[str, Any], dict[str, Any], RequestExpectation | None]:
    require(
        isinstance(account, str)
        and account.startswith("DEMO-")
        and len(account) <= 80
        and isinstance(event_id, UUID)
        and isinstance(role, str)
        and role in KINDS
        and isinstance(request_id, str)
        and identifier(request_id),
        "SUBMISSION_LINK_SCOPE_INVALID",
    )
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as c, c.begin():
        c.execute(text("SET TRANSACTION READ ONLY"))
        c.execute(text("SET LOCAL statement_timeout = '2s'"))
        c.execute(text("SET LOCAL lock_timeout = '200ms'"))
        where = (events.c.event_id == str(event_id), events.c.account_id == account)
        size = c.execute(
            select(func.octet_length(events.c.payload)).where(*where)
        ).scalar_one_or_none()
        require(
            type(size) is int and 0 < size <= MAX_PAYLOAD,
            "SUBMISSION_LINK_EVENT_MISSING_OR_TOO_LARGE",
        )
        row = (
            c.execute(select(events, outbox.c.topic).outerjoin(outbox).where(*where))
            .mappings()
            .one()
        )
        require(
            row["topic"] == "DEMO_AUDIT"
            and row["kind"] == KINDS[role]
            and type(row["fence"]) is int
            and row["fence"] > 0
            and isinstance(row["as_of"], datetime)
            and row["as_of"].tzinfo is not None,
            "SUBMISSION_LINK_INTENT_METADATA_INVALID",
        )
        raw = row["payload"].encode("utf-8")
        require(len(raw) == size, "SUBMISSION_LINK_EVENT_SIZE_CHANGED")
        envelope = dict(
            event_id=str(event_id),
            account=account,
            fence=row["fence"],
            as_of=row["as_of"].astimezone(UTC).isoformat(),
            kind=row["kind"],
            payload_sha256=hashlib.sha256(raw).hexdigest(),
            topic=row["topic"],
        )
        payload = _json(raw)
    orders, requests = payload.get("orders"), payload.get("requests")
    require(
        payload.get("mode") == "DEMO"
        and isinstance(orders, dict)
        and 1 <= len(orders) <= 128
        and isinstance(requests, dict)
        and len(requests) <= 768
        and requests.get(request_id) == "SUBMITTING",
        "SUBMISSION_LINK_INTENT_PAYLOAD_INVALID",
    )
    assert isinstance(orders, dict)
    matches = []
    for key, order in orders.items():
        require(isinstance(order, dict), "SUBMISSION_LINK_ORDER_INVALID")
        # A repeated identifier under any role or another parent is ambiguous.
        for field in ("client_id", "algo_id", "tp_algo_id", "exit_id"):
            if order.get(field) == request_id:
                matches.append((key, field, order))
    require(len(matches) == 1, "SUBMISSION_LINK_REQUEST_RELATION_AMBIGUOUS")
    key, field, order = matches[0]
    require(
        field == {"ENTRY": "client_id", "STOP_LOSS": "algo_id", "EXIT": "exit_id"}[role],
        "SUBMISSION_LINK_ROLE_MISMATCH",
    )
    signal = order.get("signal")
    require(
        isinstance(signal, dict)
        and signal.get("signal_id") == key
        and signal.get("environment") == "DEMO"
        and signal.get("symbol") == "BTCUSDT"
        and isinstance(signal.get("direction"), str)
        and signal.get("direction") in {"LONG", "SHORT"},
        "SUBMISSION_LINK_SIGNAL_INVALID",
    )
    try:
        UUID(key)
    except (ValueError, TypeError, AttributeError):
        require(False, "SUBMISSION_LINK_SIGNAL_INVALID")
    side: Literal["BUY", "SELL"] = "BUY" if signal["direction"] == "LONG" else "SELL"
    expectation = None
    if role == "ENTRY":
        require(order.get("state") == "SUBMITTING", "SUBMISSION_LINK_ENTRY_STATE_INVALID")
        quantity(order.get("quantity"))
        expectation = RequestExpectation("ENTRY", request_id, side, "LIMIT", order["quantity"])
    elif role == "STOP_LOSS":
        require(
            quantity(order.get("open_quantity")) > 0
            and order.get("position_state") not in {"CLOSED", "EXIT_PENDING"},
            "SUBMISSION_LINK_ALGO_STATE_INVALID",
        )
        expectation = RequestExpectation(
            "STOP_LOSS", request_id, "SELL" if side == "BUY" else "BUY", "STOP_MARKET", None
        )
    else:
        require(
            order.get("exit_state") == "SUBMITTING"
            and order.get("position_state") == "EXIT_PENDING",
            "SUBMISSION_LINK_EXIT_STATE_INVALID",
        )
        # _flatten queries remote positionRisk AFTER this intent. Its eventual
        # wire quantity is not the original entry quantity or local open_quantity.
    return envelope, order, expectation


def inspect_intent_reference(
    engine: Engine, account: str, event_id: UUID, role: str, request_id: str
) -> dict[str, Any]:
    """Content-pin preview only; reading a mutable row cannot authenticate its past."""
    envelope, _, expectation = _intent(engine, account, event_id, role, request_id)
    return dict(
        policy=POLICY,
        intent_reference_review="CONTENT_PIN_PREVIEW_ONLY_KEEP_PAUSED",
        envelope_sha256=digest(envelope),
        request_reference_sha256=hashlib.sha256(request_id.encode()).hexdigest(),
        intent_kind=envelope["kind"],
        role=role,
        expectation_derivable=expectation is not None,
        pin_authority="CALLER_SUPPLIED_CONTENT_DIGEST_ONLY",
        local_submission_binding_verified=False,
        remote_account_identity_verified=False,
        database_changed=False,
        filesystem_changed=False,
        http_attempts=0,
        execution_approved=False,
        orders_enabled=False,
        live_trading_enabled=False,
    )


def review_identity_capability(
    store: ResponseEvidenceStore,
    directory: Path,
    scope: str,
    journal: UUID,
    account: str,
    sequence: int,
    body: bytes,
) -> dict[str, Any]:
    row = _observation(store, directory, scope, journal, account, sequence, body)
    req = row["request"]
    require(
        req["authenticated"] is True
        and req["path"] in {"/fapi/v1/accountConfig", "/fapi/v3/account"}
        and row["response"]["status"] == 200,
        "SUBMISSION_LINK_ACCOUNT_OBSERVATION_REQUIRED",
    )
    _json(body)
    # No uid/accountAlias supplied inside an arbitrary JSON field is trusted.
    # V2 has neither a specified remote-identity contract nor credential-session binding.
    return dict(
        identity_capability="CURRENT_PROTOCOL_CANNOT_BIND_REMOTE_ACCOUNT",
        audited_account_body_digest_matches=True,
        identity_evidence_hash=row["hash"],
        remote_account_identity_verified=False,
        credential_session_binding_verified=False,
        local_fingerprint_is_account_identity=False,
        identity_contract_supported=False,
        execution_approved=False,
    )


def review_submission_link(
    store: ResponseEvidenceStore,
    directory: Path,
    scope: str,
    journal: UUID,
    account: str,
    pin: IntentPin,
    role: str,
    request_id: str,
    sequence: int,
    body: bytes,
    *,
    identity_observation: tuple[int, bytes] | None = None,
) -> dict[str, Any]:
    require(type(pin) is IntentPin, "SUBMISSION_LINK_PIN_REQUIRED")
    envelope, intent_order, expectation = _intent(
        store.engine, account, pin.event_id, role, request_id
    )
    require(digest(envelope) == pin.envelope_sha256, "SUBMISSION_LINK_PIN_MISMATCH")
    # Even blocked EXIT reviews must verify the response bytes and original-ID query.
    observed = _observation(store, directory, scope, journal, account, sequence, body)
    req = observed["request"]
    path = "/fapi/v1/algoOrder" if role == "STOP_LOSS" else "/fapi/v1/order"
    params = (
        {"clientAlgoId": request_id}
        if role == "STOP_LOSS"
        else {"symbol": "BTCUSDT", "origClientOrderId": request_id}
    )
    require(
        req["method"] == "GET"
        and req["authenticated"] is True
        and req["path"] == path
        and req["params"] == params,
        "SUBMISSION_LINK_ORIGINAL_ID_QUERY_REQUIRED",
    )
    blockers = [
        "LEGACY_INTENT_NOT_AUTHENTICATED_WIRE_ENVELOPE",
        "REMOTE_ACCOUNT_IDENTITY_NOT_BOUND",
        "CREDENTIAL_SESSION_CONTINUITY_NOT_PROVEN",
        "SUBMISSION_PRECEDENCE_NOT_PROVEN",
        "FRESH_EXCHANGE_STATE_NOT_PROVEN",
    ]
    finding: dict[str, Any] = dict(
        observation="EXIT_WIRE_QUANTITY_NOT_PERSISTED", terminal_order_observed=False
    )
    if expectation is None:
        blockers.append("EXIT_WIRE_QUANTITY_NOT_PERSISTED")
    else:
        finding = review_request_outcome(
            store, directory, scope, journal, account, sequence, body, expectation
        )
        require(finding["evidence_hash"] == observed["hash"], "SUBMISSION_LINK_OBSERVATION_CHANGED")
    terms_match = False
    if expectation is not None and observed["response"]["status"] == 200:
        raw = _json(body)
        signal = intent_order["signal"]
        if role == "ENTRY":
            require(
                quantity(raw.get("price")) == quantity(signal.get("suggested_limit_price"))
                and raw.get("timeInForce") == "GTC",
                "SUBMISSION_LINK_ENTRY_TERMS_MISMATCH",
            )
        else:
            require(
                quantity(raw.get("triggerPrice")) == quantity(signal.get("initial_stop_price"))
                and raw.get("algoType") == "CONDITIONAL"
                and raw.get("workingType") == "MARK_PRICE"
                and raw.get("priceProtect") is False,
                "SUBMISSION_LINK_ALGO_TERMS_MISMATCH",
            )
        terms_match = True
    identity: dict[str, Any] = dict(
        identity_capability="NO_AUDITED_IDENTITY_OBSERVATION_PROVIDED",
        remote_account_identity_verified=False,
        credential_session_binding_verified=False,
    )
    if identity_observation is not None:
        require(
            isinstance(identity_observation, tuple) and len(identity_observation) == 2,
            "SUBMISSION_LINK_IDENTITY_ARGUMENT_INVALID",
        )
        identity = review_identity_capability(
            store,
            directory,
            scope,
            journal,
            account,
            identity_observation[0],
            identity_observation[1],
        )
    return finding | dict(
        submission_link_review="PINNED_LOCAL_INTENT_MATCHED_KEEP_PAUSED",
        policy=POLICY,
        expectation_source="PIN_MATCHED_LOCAL_INTENT_SNAPSHOT",
        intent_envelope_sha256=pin.envelope_sha256,
        request_reference_sha256=hashlib.sha256(request_id.encode()).hexdigest(),
        role=role,
        evidence_hash=observed["hash"],
        evidence_sequence=sequence,
        local_intent_content_matches=True,
        intent_order_terms_match=terms_match,
        local_intent_is_immutable_authenticated_record=False,
        intent_is_unique_original_submission_proven=False,
        pin_authority="CALLER_SUPPLIED_CONTENT_DIGEST_ONLY",
        wire_submission_occurred_verified=False,
        local_submission_binding_verified=False,
        remote_account_identity_verified=False,
        credential_session_binding_verified=False,
        submission_precedence_verified=False,
        cross_source_atomic_snapshot_verified=False,
        fresh_exchange_state_verified=False,
        remote_request_resolution_verified=False,
        position_closed_verified=False,
        no_send_verified=False,
        archive_authorized=False,
        identity_review=identity,
        remaining_blockers=blockers,
        reconciliation_performed=False,
        database_changed=False,
        filesystem_changed=False,
        http_attempts=0,
        order_writes=0,
        execution_approved=False,
        orders_enabled=False,
        live_trading_enabled=False,
    )
