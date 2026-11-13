"""Manifest shape checks are never source authenticity or restore permission."""

from uuid import uuid4

import pytest

from packages.persistence.demo_order_quota import LocalQuotaStopped
from packages.persistence.demo_response_evidence import _line
from services.execution.demo_wire_restore_reference_candidate import (
    ANCHOR_POLICY,
    POLICY,
    parse_reference,
    review_restored_reference,
)


def value():
    return dict(
        policy=POLICY,
        anchor_policy=ANCHOR_POLICY,
        environment="SYNTHETIC_ONLY",
        binding=dict(
            account="DEMO-test",
            scope="DEMO-wire-scope",
            journal=str(uuid4()),
            installation=str(uuid4()),
            binding_hash="a" * 64,
            wire_root_hash="a" * 64,
        ),
        checkpoint=dict(sequence=0, checkpoint_hash="a" * 64, wire_hash="a" * 64),
        files=dict(wire_bytes=1, wire_sha256="a" * 64, witness_bytes=1, witness_sha256="a" * 64),
        financial=dict(
            state_sha256="a" * 64,
            account_row_sha256="a" * 64,
            history_catalog_sha256="a" * 64,
            history_rows=1,
            history_bytes=1,
            history_max_row_bytes=1,
        ),
        state=dict(
            records=1,
            sessions=0,
            operations=0,
            financial_prepares=0,
            pending_operations=0,
            uncertain_outcome=False,
            paused=True,
            unresolved_local_requests=0,
        ),
    )


@pytest.mark.parametrize(
    "group,key,replacement",
    [
        (None, "environment", "LIVE"),
        (None, "policy", "OTHER"),
        ("binding", "account", "LIVE-test"),
        ("binding", "journal", "invalid"),
        ("binding", "binding_hash", "g" * 64),
        ("checkpoint", "sequence", True),
        ("checkpoint", "sequence", 1),
        ("files", "wire_bytes", -1),
        ("files", "wire_sha256", "a" * 63),
        ("financial", "history_rows", 0),
        ("financial", "history_bytes", 536870913),
        ("financial", "history_max_row_bytes", 2),
        ("state", "pending_operations", 1),
        ("state", "uncertain_outcome", True),
        ("state", "paused", "true"),
        ("state", "unresolved_local_requests", 769),
        ("state", "sessions", 2),
        ("state", "records", 1.0),
    ],
)
def test_inconsistent_or_unsupported_manifest_is_rejected(group, key, replacement):
    data = value()
    target = data if group is None else data[group]
    target[key] = replacement
    with pytest.raises(LocalQuotaStopped):
        parse_reference(_line(data))


@pytest.mark.parametrize("group", [None, "binding", "financial", "state"])
def test_extra_authority_or_secret_fields_are_not_accepted(group):
    data = value()
    target = data if group is None else data[group]
    target["execution_approved"] = True
    with pytest.raises(LocalQuotaStopped, match="SCHEMA_INVALID"):
        parse_reference(_line(data))


@pytest.mark.parametrize("body", [b"", b"[]", b"{", b"\xffPRIVATE", b"x" * 8193])
def test_bad_or_oversized_bytes_are_rejected(body):
    with pytest.raises(LocalQuotaStopped):
        parse_reference(body)


def test_noncanonical_and_duplicate_bytes_are_rejected():
    data = value()
    valid = _line(data)
    assert parse_reference(valid) == data
    with pytest.raises(LocalQuotaStopped, match="CANONICAL_BYTES"):
        parse_reference(valid + b" ")
    with pytest.raises(LocalQuotaStopped):
        parse_reference(valid[:-1])
    with pytest.raises(LocalQuotaStopped, match="DUPLICATE"):
        parse_reference(valid.replace(b"{", b'{"policy":"duplicate",', 1))


def test_self_supplied_current_state_has_no_automatic_pin_fallback():
    with pytest.raises(LocalQuotaStopped, match="SEPARATELY_RETAINED_DIGEST"):
        review_restored_reference(None, None, None, None, None, _line(value()))
