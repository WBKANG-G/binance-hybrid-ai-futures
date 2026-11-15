"""Clock + auxiliary + synthetic archive/content on disposable PG/SQLite copies."""

# ruff: noqa: F401,F811
import json
from uuid import uuid4

import pytest
from sqlalchemy import text

from packages.persistence.demo_order_quota import LocalQuotaStopped
from services.execution import demo_copy_evidence_rehearsal as module
from services.execution.demo_evidence_snapshot import evidence_snapshot
from tests.integration.test_copy_clock_rehearsal import call as clock_call
from tests.integration.test_copy_clock_rehearsal import rehearsal
from tests.integration.test_read_audit_bootstrap import ready
from tests.integration.test_response_evidence_bootstrap import setup_ready
from tests.integration.test_runtime_read_accounting import financial
from tests.integration.test_shared_weight_checkpoint import anchored, demo_system
from tests.unit.test_copy_account_content import account_body


def call(case, **kw):
    a, writer, current, before, sleep = case
    return module.rehearse_synthetic_copy_evidence(
        a[1].engine,
        writer,
        "DEMO-test",
        a[3],
        a[0].scope,
        a[4],
        a[1].installation_id(),
        kw.pop("pin", evidence_snapshot(a[1].engine, a[3])["sha256"]),
        kw.pop("archive", a[3] / ("synthetic-response-" + uuid4().hex)),
        kw.pop("body", json.dumps(account_body()).encode()),
        copy_only=True,
        synthetic_only=True,
        clocks=current,
        **kw,
    )


def test_explicit_synthetic_copy_guard_precedes_every_access():
    with pytest.raises(LocalQuotaStopped, match="SYNTHETIC_COPY_REQUIRED"):
        module.rehearse_synthetic_copy_evidence(*([None] * 10))


def test_clock_then_pipeline_preserves_finance_and_retains_only_synthetic_artifact(rehearsal):
    a, writer, current, before, sleep = rehearsal
    fin = financial(a)
    clock = clock_call(rehearsal)
    stages = []
    report = call(rehearsal, progress=stages.append)
    assert report["synthetic_sender_calls"] == 1 and report["exchange_http_attempts"] == 0
    assert report["audit_events_before"] == clock["audit_events_after"]
    assert report["audit_events_after"] == report["audit_events_before"] + 2
    assert report["content_review"]["positive_usdt_wallet_observed"]
    assert not report["content_review"]["caller_claims_present"]
    assert report["protected_rows_and_files_preserved"] and financial(a) == fin
    assert stages == [
        "VERIFY_REHEARSED_COPY",
        "CONFIGURE_COPY_AUXILIARY_ONLY",
        "SYNTHETIC_GET_AND_ARCHIVE",
        "READ_ONLY_CONTENT_AND_PRESERVATION_CHECK",
    ]
    assert not report["installation_ready"] and not report["execution_approved"]
    after = evidence_snapshot(a[1].engine, a[3])
    with pytest.raises(LocalQuotaStopped, match="ALREADY_BOUND_NO_RETRY"):
        call(rehearsal)
    assert evidence_snapshot(a[1].engine, a[3]) == after


@pytest.mark.parametrize("fault", ["clock", "pin", "body", "archive"])
def test_preflight_denial_does_not_create_auxiliary_or_artifact(rehearsal, fault):
    a, writer, current, before, sleep = rehearsal
    if fault != "clock":
        clock_call(rehearsal)
    opts = {}
    if fault == "pin":
        opts["pin"] = "0" * 64
    if fault == "body":
        opts["body"] = b"{}"
    if fault == "archive":
        opts["archive"] = a[3].parent / "outside"
    before = evidence_snapshot(a[1].engine, a[3])
    with pytest.raises(LocalQuotaStopped):
        call(rehearsal, **opts)
    assert evidence_snapshot(a[1].engine, a[3]) == before


@pytest.mark.parametrize("fault", ["auxiliary", "archive", "content", "protected_rows"])
def test_mid_pipeline_failure_retains_changed_copy_and_never_restarts(
    rehearsal, monkeypatch, fault
):
    a, writer, current, before, sleep = rehearsal
    clock_call(rehearsal)
    fin = financial(a)
    archive = a[3] / ("synthetic-response-" + uuid4().hex)

    def fail(*args, **kw):
        raise RuntimeError("SYNTHETIC_INJECTED_FAILURE")

    if fault == "auxiliary":
        original = module.prepare_copy_baseline_evidence

        def auxiliary(*args, **kw):
            original(*args, **kw)
            fail()

        monkeypatch.setattr(module, "prepare_copy_baseline_evidence", auxiliary)
    elif fault == "archive":
        from services.execution import demo_copy_response_archive

        original = demo_copy_response_archive._write_new

        def write(path, body):
            if path.name == "response.bin":
                fail()
            original(path, body)

        monkeypatch.setattr(demo_copy_response_archive, "_write_new", write)
    elif fault == "content":
        monkeypatch.setattr(module, "review_account_evidence_inputs", fail)
    else:
        with writer.begin() as c:
            c.execute(text("CREATE TABLE unrelated_guard (id int PRIMARY KEY)"))
        original = module.review_account_evidence_inputs

        def changed(*args, **kw):
            report = original(*args, **kw)
            with writer.begin() as c:
                c.execute(text("INSERT INTO unrelated_guard VALUES (1)"))
            return report

        monkeypatch.setattr(module, "review_account_evidence_inputs", changed)
    with pytest.raises((LocalQuotaStopped, RuntimeError)):
        call(rehearsal, archive=archive)
    assert financial(a) == fin
    with pytest.raises(LocalQuotaStopped):
        call(rehearsal, archive=archive)
    if fault != "auxiliary":
        assert archive.exists()
