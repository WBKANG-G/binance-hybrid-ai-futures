"""Real PG and file failure injection, synthetic GET responses, no retry/repair."""

# ruff: noqa: F401,F811
import pytest
from sqlalchemy import event, text

from integrations.binance_usdm.rest import RESTResponse
from packages.persistence.demo_order_quota import LocalQuotaStopped
from services.execution.demo_evidence_snapshot import evidence_snapshot
from tests.integration.test_demo_read_audit import audited, records
from tests.integration.test_response_evidence_candidate import (
    evidence_rows,
    evidenced,
    review,
    transport,
)
from tests.integration.test_runtime_read_accounting import financial, signed
from tests.integration.test_shared_weight_checkpoint import anchored, demo_system
from tests.unit.test_quota_startup_read_only import files


@pytest.mark.parametrize("phase", ["RESERVE", "FINISH"])
@pytest.mark.parametrize("fault", ["write_zero", "fsync", "after_fsync"])
def test_file_boundaries_preserve_uncertainty_and_prevent_automatic_repeat(
    evidenced, monkeypatch, phase, fault
):
    import packages.persistence.demo_response_evidence as module

    a = evidenced[0]
    before = financial(a)
    seen = []
    r = transport(
        evidenced,
        lambda *args: (
            seen.append(True) or RESTResponse(200, b"{}", {"x-mbx-used-weight-1m": "100"})
        ),
    )
    old_write, old_sync, old_append = module.os.write, module.os.fsync, module._append_line

    def active():
        return bool(seen) == (phase == "FINISH")

    def write(fd, data):
        return 0 if active() else old_write(fd, data)

    def sync(fd):
        if active():
            raise OSError("SYNTHETIC_FSYNC_FAILURE")
        return old_sync(fd)

    def append(*args):
        old_append(*args)
        if active():
            raise OSError("SYNTHETIC_AFTER_FSYNC_FAILURE")

    if fault == "write_zero":
        monkeypatch.setattr(module.os, "write", write)
    if fault == "fsync":
        monkeypatch.setattr(module.os, "fsync", sync)
    if fault == "after_fsync":
        monkeypatch.setattr(module, "_append_line", append)
    with pytest.raises(LocalQuotaStopped):
        r(*signed())
    assert len(seen) == (phase == "FINISH")
    assert [x["phase"] for x in evidence_rows(evidenced)] == (
        ["RESERVE"] if phase == "FINISH" else []
    )
    # SQL rollback preserves prior V1/V2 rows. A zero-byte failure leaves
    # no file gap; forensic review must still expose the pending FINISH case.
    assert [x["action"]["kind"] for x in records(a)] == (["RESERVE"] if phase == "FINISH" else [])
    retained = evidence_snapshot(a[1].engine, a[3])
    with pytest.raises(LocalQuotaStopped):
        r(*signed())
    if fault == "write_zero":
        report = review(evidenced)
        assert report["pending_requests"] == (phase == "FINISH")
        if phase == "FINISH":
            with pytest.raises(LocalQuotaStopped, match="UNFINISHED_REQUESTS"):
                transport(evidenced)
        else:
            # Explicit construction after an entirely rolled-back unsent attempt
            # is allowed, but does not send, retry, or grant execution authority.
            reopened = transport(evidenced)
            assert reopened is not r
    else:
        for attempt in (lambda: review(evidenced), lambda: transport(evidenced)):
            with pytest.raises(LocalQuotaStopped):
                attempt()
    assert evidence_snapshot(a[1].engine, a[3]) == retained
    assert financial(a) == before and len(seen) == (phase == "FINISH")


@pytest.mark.parametrize(
    "sql",
    [
        "ALTER TABLE read_response_evidence_events_v2 ADD COLUMN forged text",
        "ALTER TABLE read_response_evidence_events_v2 ALTER COLUMN response_state TYPE varchar(80)",
        "ALTER TABLE read_response_evidence_events_v2 ALTER COLUMN response_state DROP NOT NULL",
        "ALTER TABLE read_response_evidence_events_v2 DROP CONSTRAINT read_response_evidence_events_v2_scope_fkey",
        "CREATE INDEX forbidden_extra_index ON read_response_evidence_events_v2 (phase)",
        "ALTER TABLE read_response_evidence_events_v2 ADD CONSTRAINT forbidden_check CHECK (sequence>=0)",
    ],
)
def test_schema_shape_changes_are_rejected_before_sender(evidenced, sql):
    a = evidenced[0]
    seen = []
    r = transport(evidenced, lambda *args: seen.append(True))
    with a[1].engine.begin() as c:
        c.execute(text(sql))
    retained = evidence_snapshot(a[1].engine, a[3])
    with pytest.raises(LocalQuotaStopped, match="TABLE_"):
        r(*signed())
    assert not seen and evidence_snapshot(a[1].engine, a[3]) == retained
