"""Malformed proof labels cannot access a store or grant identity authority."""

from uuid import uuid4

import pytest

from packages.persistence.demo_order_quota import LocalQuotaStopped
from services.execution.demo_submission_link_candidate import IntentPin, inspect_intent_reference


@pytest.mark.parametrize("value", ["", "a" * 63, "g" * 64, "A" * 64, True, None])
def test_pin_requires_exact_content_digest(value):
    with pytest.raises(LocalQuotaStopped, match="PIN_INVALID"):
        IntentPin(uuid4(), value)


def test_string_event_id_is_not_an_immutable_reference_object():
    with pytest.raises(LocalQuotaStopped, match="PIN_INVALID"):
        IntentPin(str(uuid4()), "a" * 64)


@pytest.mark.parametrize(
    "account,role,request_id",
    [
        ("LIVE-test", "ENTRY", "one"),
        ("DEMO-test", "TP", "one"),
        ("DEMO-test", "ENTRY", "invalid?"),
        ("DEMO-test", [], "one"),
    ],
)
def test_invalid_scope_rejected_before_database_access(account, role, request_id):
    with pytest.raises(LocalQuotaStopped, match="SCOPE_INVALID"):
        inspect_intent_reference(None, account, uuid4(), role, request_id)
