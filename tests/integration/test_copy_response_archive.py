"""Synthetic sender only; no live credentials, HTTP, adoption or source start."""

# ruff: noqa: F401,F811
import hashlib
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from integrations.binance_usdm.rest import RESTResponse, prepare_signed_query
from packages.persistence.demo_order_quota import LocalQuotaStopped
from packages.persistence.demo_response_evidence import (
    EvidenceReadJournal,
    ResponseEvidenceStore,
    events,
)
from services.execution import demo_copy_response_archive as module
from services.execution.demo_copy_response_archive import (
    CopyAccountArchiveTransport,
    review_response_archive,
)
from tests.integration.test_copy_baseline_evidence import call, stale_copy
from tests.integration.test_demo_read_audit import records
from tests.integration.test_read_audit_bootstrap import ready
from tests.integration.test_response_evidence_bootstrap import setup_ready, shared
from tests.integration.test_runtime_read_accounting import financial, settings
from tests.integration.test_runtime_read_accounting import signed as old_signed
from tests.integration.test_shared_weight_checkpoint import anchored, demo_system


def fresh(a):
    query = prepare_signed_query(
        {},
        api_key=SecretStr("SYNTHETIC_KEY"),
        secret=SecretStr("SYNTHETIC_SECRET"),
        timestamp_ms=int(a[2].utc().timestamp() * 1000),
    )
    return "GET", "/fapi/v3/account", query.query, query.api_key


@pytest.fixture
def capture(stale_copy, tmp_path):
    a = stale_copy
    call(a, apply=True, readers_stopped=True)
    audit, _ = shared(a)
    journal = EvidenceReadJournal(
        a[3],
        a[0].scope,
        a[4],
        anchors=a[1],
        audit=audit,
        evidence=ResponseEvidenceStore(a[1].engine),
        account="DEMO-test",
        enable_evidence_binding=True,
        read_only=False,
        **a[5],
    )
    parent = tmp_path / "archive"
    parent.mkdir(mode=0o700)
    sent = []

    def build(*, sender=None, directory=None, **kw):
        def send(*args):
            sent.append(args[1])
            return RESTResponse(200, b'{"totalWalletBalance":"2"}', {"x-mbx-used-weight-1m": "100"})

        return CopyAccountArchiveTransport(
            settings(a),
            journal,
            directory or parent / "one",
            copy_only=True,
            enable_capture=True,
            credential_context_id=str(uuid4()),
            expected_api_key=old_signed()[3],
            sender=sender or send,
            monotonic=a[2].mono,
            **kw,
        )

    return a, journal, parent, sent, build


def test_disabled_guard_precedes_provider_files_and_sender():
    with pytest.raises(LocalQuotaStopped, match="COPY_ARCHIVE_DISABLED"):
        CopyAccountArchiveTransport(
            None, None, None, credential_context_id="", expected_api_key=None, sender=None
        )


def test_success_archives_exact_bytes_and_completed_audit_without_approving_baseline(capture):
    a, journal, parent, sent, build = capture
    before = financial(a)
    transport = build()
    response = transport(*fresh(a))
    report = transport.archive_report
    assert response.body == (parent / "one/response.bin").read_bytes()
    assert (
        report["response_body_artifact_stored"]
        and report["credential_context_caller_attested"]
        and not report["credential_context_operator_attested"]
    )
    for flag in (
        "remote_account_identity_verified",
        "whole_history_coverage_verified",
        "midnight_valuation_verified",
        "baseline_source_accepted",
        "baseline_adoption_approved",
        "execution_approved",
        "orders_enabled",
    ):
        assert not report[flag]
    assert financial(a) == before and sent == ["/fapi/v3/account"]
    with a[1].engine.connect() as c:
        assert c.execute(select(events.c.phase).order_by(events.c.sequence)).scalars().all() == [
            "RESERVE",
            "FINISH",
        ]
    bytes_before = {p.name: p.read_bytes() for p in (parent / "one").iterdir()}
    assert review_response_archive(journal, parent / "one", report["receipt_sha256"])[
        "response_body_artifact_stored"
    ]
    assert {p.name: p.read_bytes() for p in (parent / "one").iterdir()} == bytes_before
    assert all(
        b"SYNTHETIC_KEY" not in body and b"a" * 64 not in body for body in bytes_before.values()
    )
    with pytest.raises(LocalQuotaStopped, match="NO_RETRY"):
        transport(*fresh(a))
    with pytest.raises(LocalQuotaStopped, match="NO_RESUME"):
        build()
    assert len(sent) == 1


@pytest.mark.parametrize("fault", ["key", "path", "post"])
def test_wrong_request_never_creates_archive_or_sends(capture, fault):
    a, journal, parent, sent, build = capture
    t = build()
    args = list(fresh(a))
    if fault == "key":
        args[3] = SecretStr("OTHER_SYNTHETIC")
    if fault == "path":
        args[1] = "/fapi/v3/positionRisk"
    if fault == "post":
        args[0] = "POST"
    with pytest.raises(LocalQuotaStopped):
        t(*args)
    assert not (parent / "one").exists() and not sent and t.archive_report is None


@pytest.mark.parametrize(
    "fault",
    ["network", "oversized", "key_echo", "signature_echo", "missing_weight", "http400", "http429"],
)
def test_ambiguous_or_error_responses_preserve_partial_and_never_retry(capture, fault):
    a, journal, parent, sent, build = capture

    def sender(*args):
        sent.append(1)
        if fault == "network":
            raise OSError("synthetic failure")
        body = b"{}"
        if fault == "oversized":
            body = b"0" * (module.MAX_BODY + 1)
        if fault == "key_echo":
            body = b"SYNTHETIC_KEY"
        if fault == "signature_echo":
            body = args[2].get_secret_value().split("signature=")[1].encode()
        headers = {} if fault == "missing_weight" else {"x-mbx-used-weight-1m": "100"}
        status = {"http400": 400, "http429": 429}.get(fault, 200)
        return RESTResponse(status, body, headers)

    t = build(sender=sender)
    fin = financial(a)
    with pytest.raises(LocalQuotaStopped):
        t(*fresh(a))
    assert t.archive_report is None and (parent / "one/intent.json").is_file()
    assert not (parent / "one/receipt.json").exists() and financial(a) == fin
    if fault in {"key_echo", "signature_echo", "oversized", "network"}:
        assert not (parent / "one/response.bin").exists()
    with pytest.raises(LocalQuotaStopped, match="NO_RETRY"):
        t(*fresh(a))
    with pytest.raises(LocalQuotaStopped, match="NO_RESUME"):
        build(sender=sender)
    assert len(sent) == 1


@pytest.mark.parametrize("fault", ["intent_write", "body_write", "receipt_write", "receipt_fsync"])
def test_write_faults_never_retry_or_remove_partial(capture, monkeypatch, fault):
    a, journal, parent, sent, build = capture
    t = build()
    original = module._write_new
    target = {
        "intent_write": "intent.json",
        "body_write": "response.bin",
        "receipt_write": "receipt.json",
    }.get(fault)

    def write(path, body):
        if path.name == target:
            raise OSError("synthetic disk failure")
        original(path, body)

    monkeypatch.setattr(module, "_write_new", write)
    if fault == "receipt_fsync":
        sync = module._sync_directory

        def bad_sync(path):
            if (path / "receipt.json").exists():
                raise OSError("synthetic fsync failure")
            sync(path)

        monkeypatch.setattr(module, "_sync_directory", bad_sync)
    with pytest.raises((LocalQuotaStopped, OSError)):
        t(*fresh(a))
    assert t.archive_report is None and (parent / "one").is_dir()
    with pytest.raises(LocalQuotaStopped, match="NO_RETRY"):
        t(*fresh(a))
    assert len(sent) == (0 if fault == "intent_write" else 1)


@pytest.mark.parametrize("fault", ["body", "intent", "receipt", "extra", "symlink", "wrong_pin"])
def test_review_rejects_tampered_or_unpinned_archive(capture, fault):
    a, journal, parent, sent, build = capture
    t = build()
    t(*fresh(a))
    directory = parent / "one"
    pin = t.archive_report["receipt_sha256"]
    if fault in {"body", "intent", "receipt"}:
        name = {"body": "response.bin", "intent": "intent.json", "receipt": "receipt.json"}[fault]
        (directory / name).write_bytes(b"{}")
    elif fault == "extra":
        (directory / "extra").write_text("x")
    elif fault == "symlink":
        (directory / "response.bin").rename(directory / "saved")
        (directory / "response.bin").symlink_to(directory / "saved")
        (directory / "saved").rename(parent / "saved")
        (directory / "response.bin").unlink()
        (directory / "response.bin").symlink_to(parent / "saved")
    else:
        pin = "0" * 64
    with pytest.raises((LocalQuotaStopped, KeyError, ValueError, OSError)):
        review_response_archive(journal, directory, pin)
    assert len(sent) == 1


@pytest.mark.parametrize("fault", ["old", "future"])
def test_stale_signed_query_denied_before_directory_or_send(capture, fault):
    a, journal, parent, sent, build = capture
    args = list(fresh(a))
    offset = -5001 if fault == "old" else 5001
    query = prepare_signed_query(
        {},
        api_key=args[3],
        secret=SecretStr("SYNTHETIC_SECRET"),
        timestamp_ms=int(a[2].utc().timestamp() * 1000) + offset,
    )
    args[2] = query.query
    with pytest.raises(LocalQuotaStopped, match="FRESH_SIGNATURE_REQUIRED"):
        build()(*args)
    assert not (parent / "one").exists() and not sent


@pytest.mark.parametrize("fault", ["day_boundary", "wall_drift", "elapsed"])
def test_clock_or_day_change_never_publishes_success(capture, fault):
    a, journal, parent, sent, build = capture
    original = module._observed

    # Advance after the completed audit read, before publishing the receipt.
    def observed(*args):
        value = original(*args)
        if fault == "day_boundary":
            a[2].seconds += 86400
        elif fault == "wall_drift":
            a[2].wall_delta -= 10
        else:
            a[2].seconds += 76
        return value

    from unittest.mock import patch

    t = build()
    fin = financial(a)
    with patch.object(module, "_observed", observed):
        with pytest.raises(LocalQuotaStopped, match="CAPTURE_WINDOW_CHANGED"):
            t(*fresh(a))
    assert t.archive_report is None and not (parent / "one/receipt.json").exists()
    assert (parent / "one/response.bin").is_file() and financial(a) == fin
    with pytest.raises(LocalQuotaStopped, match="NO_RETRY"):
        t(*fresh(a))
    assert len(sent) == 1


@pytest.mark.parametrize("fault", ["public_parent", "symlink_parent", "existing_destination"])
def test_unsafe_archive_path_denied_before_wire(capture, fault):
    a, journal, parent, sent, build = capture
    destination = parent / "one"
    if fault == "public_parent":
        parent.chmod(0o755)
    elif fault == "symlink_parent":
        link = parent.parent / "linked"
        link.symlink_to(parent, target_is_directory=True)
        destination = link / "one"
    else:
        destination.mkdir(mode=0o700)
    with pytest.raises(LocalQuotaStopped):
        build(directory=destination)
    assert not sent


def test_finish_commit_failure_retains_body_without_success_or_retry(capture):
    from sqlalchemy import event

    a, journal, parent, sent, build = capture
    t = build()
    fin = financial(a)

    def fail_finish(connection):
        if connection.execute(select(events.c.phase).where(events.c.phase == "FINISH")).first():
            raise RuntimeError("synthetic finish commit failure")

    event.listen(a[1].engine, "commit", fail_finish)
    try:
        with pytest.raises((LocalQuotaStopped, RuntimeError)):
            t(*fresh(a))
    finally:
        event.remove(a[1].engine, "commit", fail_finish)
    assert t.archive_report is None and (parent / "one/response.bin").is_file()
    assert not (parent / "one/receipt.json").exists() and financial(a) == fin
    with pytest.raises(LocalQuotaStopped, match="NO_RETRY"):
        t(*fresh(a))
    with pytest.raises(LocalQuotaStopped):
        build()
    assert len(sent) == 1
