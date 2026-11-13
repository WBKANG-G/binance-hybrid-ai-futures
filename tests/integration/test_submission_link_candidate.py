"""Read-only legacy intent joins and audited synthetic account observations."""

# ruff: noqa: F401,F811
import json
from uuid import UUID

import pytest
from sqlalchemy import delete, event, select, update

from packages.domain.contracts import OrderState, PositionState
from packages.persistence.demo import events, outbox
from packages.persistence.demo_order_quota import LocalQuotaStopped
from services.execution.demo_submission_link_candidate import (
    IntentPin,
    inspect_intent_reference,
    review_identity_capability,
    review_submission_link,
)
from services.execution.models import PaperOrder
from tests.integration.test_demo_read_audit import audited, records
from tests.integration.test_paper import signal, snapshot
from tests.integration.test_request_outcome_candidate import observe
from tests.integration.test_request_outcome_candidate import payload as base_payload
from tests.integration.test_response_evidence_candidate import evidenced, transport
from tests.integration.test_runtime_read_accounting import financial, signed
from tests.integration.test_shared_weight_checkpoint import anchored, demo_system
from tests.unit.test_quota_startup_read_only import files

REQUEST = "synthetic-request-1"
KINDS = {
    "ENTRY": "DEMO_ENTRY_INTENT",
    "STOP_LOSS": "DEMO_SL_INTENT",
    "EXIT": "DEMO_REDUCE_ONLY_INTENT",
}


def payload(role="ENTRY", **kw):
    terms = (
        dict(price="100", timeInForce="GTC")
        if role != "STOP_LOSS"
        else dict(
            triggerPrice="90", algoType="CONDITIONAL", workingType="MARK_PRICE", priceProtect=False
        )
    )
    return base_payload(role, **(terms | kw))


def intent(case, role="ENTRY", direction="LONG"):
    a = case[0]
    store, c = a[-2:]
    sig = signal(
        environment="DEMO",
        direction=direction,
        initial_stop_price="90" if direction == "LONG" else "110",
        target_price="120" if direction == "LONG" else "80",
    )
    order = PaperOrder(
        client_id=REQUEST if role == "ENTRY" else "synthetic-parent",
        signal=sig,
        snapshot=snapshot(),
        group="CRYPTO",
        quantity="0.0007",
        quantity_step="0.0001",
        price_tick="0.1",
        loss_per_unit="10",
        budget="0.007",
        remaining="0.0007",
    )
    order.state = OrderState.SUBMITTING if role == "ENTRY" else OrderState.FILLED
    if role != "ENTRY":
        order.open_quantity, order.filled, order.remaining = "0.0007", "0.0007", "0"
        order.position_state = PositionState.OPEN_UNPROTECTED
    if role == "STOP_LOSS":
        order.algo_id = REQUEST
    elif role == "EXIT":
        order.exit_id, order.exit_state = REQUEST, "SUBMITTING"
        order.position_state = PositionState.EXIT_PENDING

    def stage(ledger):
        ledger.orders[str(sig.signal_id)] = order
        ledger.requests[REQUEST] = "SUBMITTING"

    store.change(c.lease, KINDS[role], stage)
    with store.engine.connect() as conn:
        row = conn.execute(select(events).where(events.c.kind == KINDS[role])).mappings().one()
    pin = inspect_intent_reference(store.engine, "DEMO-test", UUID(row["event_id"]), role, REQUEST)
    return IntentPin(UUID(row["event_id"]), pin["envelope_sha256"])


def call(case, pin, body, role="ENTRY", **kw):
    a, _, store, shared = case
    return review_submission_link(
        store, a[3], shared.scope, a[4], "DEMO-test", pin, role, REQUEST, 1, body, **kw
    )


def mutate(case, pin, change):
    engine = case[2].engine
    with engine.begin() as c:
        row = (
            c.execute(select(events).where(events.c.event_id == str(pin.event_id))).mappings().one()
        )
        raw = json.loads(row["payload"])
        change(raw)
        c.execute(
            update(events)
            .where(events.c.event_id == str(pin.event_id))
            .values(payload=json.dumps(raw))
        )


@pytest.mark.parametrize(
    "role,direction",
    [("ENTRY", "LONG"), ("ENTRY", "SHORT"), ("STOP_LOSS", "LONG"), ("STOP_LOSS", "SHORT")],
)
def test_expectations_come_from_pinned_intent_and_cannot_grant_authority(
    evidenced, role, direction
):
    pin = intent(evidenced, role, direction)
    side = "BUY" if (direction == "LONG") == (role == "ENTRY") else "SELL"
    body = observe(
        evidenced,
        payload(
            role,
            side=side,
            **({"triggerPrice": "110"} if role == "STOP_LOSS" and direction == "SHORT" else {}),
        ),
        role,
    )
    a = evidenced[0]
    before, disk, old = financial(a), files(a[3]), records(a)
    report = call(evidenced, pin, body, role)
    assert report["local_intent_content_matches"] and report["response_identity_matches"]
    assert report["expectation_source"] == "PIN_MATCHED_LOCAL_INTENT_SNAPSHOT"
    for field in (
        "local_submission_binding_verified",
        "remote_account_identity_verified",
        "wire_submission_occurred_verified",
        "intent_is_unique_original_submission_proven",
        "remote_request_resolution_verified",
        "submission_precedence_verified",
        "cross_source_atomic_snapshot_verified",
        "archive_authorized",
        "execution_approved",
    ):
        assert report[field] is False
    assert REQUEST not in str(report)
    assert financial(a) == before and files(a[3]) == disk and records(a) == old


def test_exit_intent_does_not_turn_local_entry_or_open_quantity_into_wire_quantity(evidenced):
    pin = intent(evidenced, "EXIT")
    body = observe(
        evidenced, payload("EXIT", origQty="0.002", executedQty="0.002", side="SELL"), "EXIT"
    )
    report = call(evidenced, pin, body, "EXIT")
    assert report["observation"] == "EXIT_WIRE_QUANTITY_NOT_PERSISTED"
    assert not report["terminal_order_observed"]
    assert "EXIT_WIRE_QUANTITY_NOT_PERSISTED" in report["remaining_blockers"]
    assert not report["local_submission_binding_verified"]


def test_changed_intent_hash_and_forged_pin_are_rejected(evidenced):
    pin = intent(evidenced)
    body = observe(evidenced, payload())
    with pytest.raises(LocalQuotaStopped, match="PIN_MISMATCH"):
        call(evidenced, IntentPin(pin.event_id, "a" * 64), body)
    mutate(evidenced, pin, lambda p: p.update(paused=not p["paused"]))
    with pytest.raises(LocalQuotaStopped, match="PIN_MISMATCH"):
        call(evidenced, pin, body)


@pytest.mark.parametrize(
    "kind", ["state", "quantity", "duplicate", "role", "environment", "symbol", "signal-id", "mode"]
)
def test_invalid_or_ambiguous_legacy_intent_cannot_supply_expectations(evidenced, kind):
    pin = intent(evidenced)

    def patch(p):
        key, order = next(iter(p["orders"].items()))
        if kind == "state":
            p["requests"][REQUEST] = "UNKNOWN"
        if kind == "quantity":
            order["quantity"] = "NaN"
        if kind == "duplicate":
            p["orders"]["another"] = order.copy()
        if kind == "role":
            order["client_id"], order["exit_id"] = "other", REQUEST
        if kind == "environment":
            order["signal"]["environment"] = "LIVE"
        if kind == "symbol":
            order["signal"]["symbol"] = "ETHUSDT"
        if kind == "signal-id":
            order["signal"]["signal_id"] = "other"
        if kind == "mode":
            p["mode"] = "LIVE"

    mutate(evidenced, pin, patch)
    with pytest.raises(LocalQuotaStopped):
        inspect_intent_reference(evidenced[2].engine, "DEMO-test", pin.event_id, "ENTRY", REQUEST)


@pytest.mark.parametrize(
    "field,value", [("kind", "DEMO_ENTRY_EFFECT"), ("account_id", "DEMO-other"), ("fence", 0)]
)
def test_event_metadata_scope_is_required(evidenced, field, value):
    pin = intent(evidenced)
    # Account foreign keys prevent arbitrary reassignment; exercise the account filter instead.
    if field == "account_id":
        with pytest.raises(LocalQuotaStopped):
            inspect_intent_reference(evidenced[2].engine, value, pin.event_id, "ENTRY", REQUEST)
    else:
        with evidenced[2].engine.begin() as c:
            c.execute(
                update(events)
                .where(events.c.event_id == str(pin.event_id))
                .values(**{field: value})
            )
        with pytest.raises(LocalQuotaStopped):
            inspect_intent_reference(
                evidenced[2].engine, "DEMO-test", pin.event_id, "ENTRY", REQUEST
            )


def test_missing_outbox_is_not_an_audited_intent(evidenced):
    pin = intent(evidenced)
    with evidenced[2].engine.begin() as c:
        c.execute(delete(outbox).where(outbox.c.event_id == str(pin.event_id)))
    with pytest.raises(LocalQuotaStopped, match="METADATA_INVALID"):
        inspect_intent_reference(evidenced[2].engine, "DEMO-test", pin.event_id, "ENTRY", REQUEST)


def test_size_limit_is_checked_before_fetching_full_ledger(evidenced):
    pin = intent(evidenced)
    engine = evidenced[2].engine
    with engine.begin() as c:
        c.execute(
            update(events)
            .where(events.c.event_id == str(pin.event_id))
            .values(payload="x" * 1048577)
        )
    statements = []

    def capture(c, cursor, statement, params, context, many):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        with pytest.raises(LocalQuotaStopped, match="TOO_LARGE"):
            inspect_intent_reference(engine, "DEMO-test", pin.event_id, "ENTRY", REQUEST)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    selects = [s for s in statements if s.startswith("SELECT")]
    assert len(selects) == 1 and "octet_length" in selects[0]
    assert any("SET TRANSACTION READ ONLY" in s for s in statements)


@pytest.mark.parametrize(
    "account_body",
    [{"totalWalletBalance": "4998.7"}, {"uid": "123456", "accountAlias": "not-a-trust-anchor"}],
)
def test_audited_account_json_cannot_manufacture_remote_identity(evidenced, account_body):
    body = json.dumps(account_body).encode()
    from integrations.binance_usdm.rest import RESTResponse

    transport(evidenced, lambda *args: RESTResponse(200, body, {"x-mbx-used-weight-1m": "100"}))(
        *signed()
    )
    a, _, store, shared = evidenced
    report = review_identity_capability(store, a[3], shared.scope, a[4], "DEMO-test", 1, body)
    assert report["audited_account_body_digest_matches"]
    assert (
        not report["remote_account_identity_verified"]
        and not report["credential_session_binding_verified"]
    )
    assert not report["local_fingerprint_is_account_identity"]
    assert "123456" not in str(report) and "not-a-trust-anchor" not in str(report)


def test_order_observation_cannot_be_used_as_account_identity(evidenced):
    body = observe(evidenced, payload())
    a, _, store, shared = evidenced
    with pytest.raises(LocalQuotaStopped, match="ACCOUNT_OBSERVATION_REQUIRED"):
        review_identity_capability(store, a[3], shared.scope, a[4], "DEMO-test", 1, body)


def test_not_found_does_not_resolve_a_pin_matched_request(evidenced):
    pin = intent(evidenced)
    body = observe(evidenced, {"code": -2013}, status=400)
    report = call(evidenced, pin, body)
    assert (
        report["local_intent_content_matches"] and report["observation"] == "NOT_FOUND_UNRESOLVED"
    )
    assert not report["no_send_verified"] and not report["remote_request_resolution_verified"]


@pytest.mark.parametrize(
    "role,patch",
    [
        ("ENTRY", {"price": "101"}),
        ("ENTRY", {"timeInForce": "GTX"}),
        ("ENTRY", {"origQty": "0.0008"}),
        ("ENTRY", {"side": "SELL"}),
        ("STOP_LOSS", {"triggerPrice": "91"}),
        ("STOP_LOSS", {"workingType": "CONTRACT_PRICE"}),
        ("STOP_LOSS", {"priceProtect": True}),
        ("STOP_LOSS", {"algoType": "OTHER"}),
    ],
)
def test_original_intent_terms_are_not_replaced_by_response_terms(evidenced, role, patch):
    pin = intent(evidenced, role)
    base = {"side": "SELL"} if role == "STOP_LOSS" else {}
    body = observe(evidenced, payload(role, **(base | patch)), role)
    with pytest.raises(LocalQuotaStopped):
        call(evidenced, pin, body, role)


def test_intent_is_read_under_one_repeatable_snapshot_and_later_change_breaks_pin(evidenced):
    pin = intent(evidenced)
    body = observe(evidenced, payload())
    engine = evidenced[2].engine
    changed = []

    def change_after_size(c, cursor, statement, params, context, many):
        if "SELECT octet_length" in statement and not changed:
            changed.append(True)
            mutate(evidenced, pin, lambda p: p.update(paused=not p["paused"]))

    event.listen(engine, "after_cursor_execute", change_after_size)
    try:
        report = call(evidenced, pin, body)
    finally:
        event.remove(engine, "after_cursor_execute", change_after_size)
    assert changed and report["local_intent_content_matches"]
    assert not report["cross_source_atomic_snapshot_verified"]
    with pytest.raises(LocalQuotaStopped, match="PIN_MISMATCH"):
        call(evidenced, pin, body)


def test_optional_audited_account_body_preserves_identity_blockers(evidenced):
    from integrations.binance_usdm.rest import RESTResponse

    pin = intent(evidenced)
    body = observe(evidenced, payload())
    account_body = b'{"uid":"untrusted-uid","accountAlias":"untrusted-label"}'
    transport(
        evidenced, lambda *args: RESTResponse(200, account_body, {"x-mbx-used-weight-1m": "100"})
    )(*signed())
    report = call(evidenced, pin, body, identity_observation=(3, account_body))
    assert report["identity_review"]["audited_account_body_digest_matches"]
    assert not report["remote_account_identity_verified"]
    assert "REMOTE_ACCOUNT_IDENTITY_NOT_BOUND" in report["remaining_blockers"]
    assert "untrusted-uid" not in str(report) and "untrusted-label" not in str(report)
    with pytest.raises(LocalQuotaStopped, match="BODY_MISMATCH"):
        call(evidenced, pin, body, identity_observation=(3, account_body + b" "))


def test_pin_matched_order_still_rejects_another_query_or_body(evidenced):
    pin = intent(evidenced)
    body = observe(evidenced, payload(), numeric=True)
    with pytest.raises(LocalQuotaStopped, match="ORIGINAL_ID_QUERY_REQUIRED"):
        call(evidenced, pin, body)
    with pytest.raises(LocalQuotaStopped, match="BODY_MISMATCH"):
        call(evidenced, pin, body + b" ")
