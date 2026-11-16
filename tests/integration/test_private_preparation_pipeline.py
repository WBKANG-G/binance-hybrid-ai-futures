"""More than 75 seconds of preparation must not spend the signing lease."""

# ruff: noqa: F401,F811
import json
from uuid import uuid4

import pytest

from packages.persistence.demo_order_quota import LocalQuotaStopped
from scripts import demo_key_session as keys
from services.execution import demo_copy_evidence_rehearsal as pipeline
from services.execution import demo_copy_private_preparation as proof_module
from services.execution import demo_copy_private_week_review as week
from services.execution.demo_evidence_snapshot import evidence_snapshot
from tests.integration.test_copy_clock_rehearsal import rehearsal
from tests.integration.test_private_week_copy import weekly_case
from tests.integration.test_read_audit_bootstrap import ready
from tests.integration.test_response_evidence_bootstrap import setup_ready
from tests.integration.test_runtime_read_accounting import financial
from tests.integration.test_shared_weight_checkpoint import anchored, demo_system
from tests.unit.test_copy_account_content import account_body
from tests.unit.test_demo_key_session import session_dir


def run(case, proof, **kw):
    inner, _, _ = case
    a, writer, current, _, _ = inner
    return week.rehearse_private_week_copy(
        a[1].engine,
        writer,
        "DEMO-test",
        a[3],
        a[0].scope,
        a[4],
        a[1].installation_id(),
        evidence_snapshot(a[1].engine, a[3])["sha256"],
        a[3] / ("synthetic-response-" + uuid4().hex),
        json.dumps(account_body()).encode(),
        proof,
        copy_only=True,
        synthetic_only=True,
        acknowledge_week_boundary_observation=True,
        clocks=current,
        **kw,
    )


def make(case, monkeypatch):
    inner, lease, _ = case
    lease.close()
    a, _, clocks, _, _ = inner
    monkeypatch.setattr(proof_module, "_readonly_tmpfs", lambda: True)
    return proof_module.SyntheticPrivatePreparation(
        "DEMO-test", a[0].scope, copy_only=True, synthetic_only=True, clocks=clocks
    )


def test_200_seconds_local_preparation_then_one_short_private_capture(weekly_case, monkeypatch):
    proof = make(weekly_case, monkeypatch)
    a = weekly_case[0][0]
    original = week.review_copy_baseline_requirements
    calls = 0

    def slow(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            a[2].seconds += 100
        return original(*args, **kwargs)

    monkeypatch.setattr(week, "review_copy_baseline_requirements", slow)
    setup = pipeline.prepare_copy_baseline_evidence

    def slow_setup(*args, **kwargs):
        a[2].seconds += 100
        return setup(*args, **kwargs)

    monkeypatch.setattr(pipeline, "prepare_copy_baseline_evidence", slow_setup)
    fin = financial(a)
    try:
        report = run(weekly_case, proof)
    finally:
        proof.close()
    assert report["pipeline"]["private_lease_opened_at_capture"] is True
    assert report["pipeline"]["private_preparation_seconds"] == 200
    assert report["pipeline"]["synthetic_sender_calls"] == 1
    assert report["pipeline"]["exchange_http_attempts"] == 0 and financial(a) == fin
    assert report["stored_day_kst"] == "2026-11-13" and report["stored_week_kst"] == "2026-11-09"
    assert not report["baseline_adoption_approved"]


@pytest.mark.parametrize(
    "fault", ["wrong_pair", "changed_before_setup", "expired_preparation", "clock_drift"]
)
def test_bad_source_cannot_reach_auxiliary_mutations(weekly_case, monkeypatch, fault):
    inner, lease, folder = weekly_case
    a = inner[0]
    lease.close()
    if fault == "wrong_pair":
        keys.clear()
        answers = iter(["OTHER_SYNTHETIC_KEY", "OTHER_SYNTHETIC_SECRET"])
        monkeypatch.setattr(keys.getpass, "getpass", lambda *args: next(answers))
        keys.register()
    before = evidence_snapshot(a[1].engine, a[3])
    proof = None
    try:
        with pytest.raises(LocalQuotaStopped):
            proof = make(weekly_case, monkeypatch)
            if fault == "changed_before_setup":
                (folder / keys.FILENAME).write_bytes(b"{}")
                before = evidence_snapshot(a[1].engine, a[3])
            if fault == "expired_preparation":
                a[2].seconds += 300
            if fault == "clock_drift":
                from dataclasses import replace
                from datetime import timedelta

                old = proof._clocks.wall_clock
                proof._clocks = replace(
                    proof._clocks, wall_clock=lambda: old() + timedelta(seconds=2)
                )
            run(weekly_case, proof)
        assert evidence_snapshot(a[1].engine, a[3]) == before
    finally:
        if proof is not None:
            proof.close()
