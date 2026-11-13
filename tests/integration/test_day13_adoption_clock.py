"""Clock preparation authenticates the completed preview before any rebase."""

# ruff: noqa: F401,F811
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import select, update

from packages.persistence.demo import accounts
from packages.persistence.demo_order_quota import LocalQuotaStopped
from packages.persistence.demo_read_audit import AuditedDemoReadJournal, ReadAuditStore
from scripts import adopt_demo_accounted_day13 as adoption
from scripts import probe_demo_accounted_reads as common
from scripts import review_demo_read_code_install as fingerprint
from scripts import review_demo_read_deployment as deployment
from tests.integration.test_accounted_day13_apply import reviewed, run
from tests.integration.test_accounted_day13_preview import day12, day_sender, prepare
from tests.integration.test_accounted_read_probe import (
    anchored,
    candidate,
    demo_system,
    installation,
    invoke,
    ready,
    sender,
)
from tests.integration.test_runtime_read_accounting import financial


def prepare_clock(case, clock=None, **kw):
    a = case[0]
    return adoption.prepare_clock(
        a[1].engine,
        deployment.STORAGE,
        a[0].scope,
        a[4],
        a[1].installation_id(),
        "DEMO-test",
        kw.pop("credentials", lambda: a[7].client.credentials),
        fingerprint,
        common,
        clocks=clock or case[3],
        apply=True,
        **kw,
    )


def clock_status(case, clock, expected=None):
    a = case[0]
    return adoption.clock_status(
        a[1].engine,
        deployment.STORAGE,
        a[0].scope,
        a[4],
        a[1].installation_id(),
        "DEMO-test",
        fingerprint,
        common,
        clocks=clock,
        expected=expected,
    )


@pytest.mark.parametrize("kind", ["boot", "wall", "backwards"])
def test_completed_preview_rebase_preserves_all_rows_and_requires_full_wait(reviewed, kind):
    a = reviewed[0]
    before = financial(a)
    clocks = dict(
        boot=replace(reviewed[3], boot=lambda: "new-adoption-boot"),
        wall=replace(reviewed[3], wall_clock=lambda: a[2].utc() + timedelta(seconds=5)),
        backwards=replace(reviewed[3], monotonic=lambda: a[2].mono() - 50),
    )
    clock = clocks[kind]
    old = common.resource_rows(a[1].engine, a[0].scope)[0]
    with pytest.raises(LocalQuotaStopped, match="SHARED_CLOCK_OR_BOOT_CHANGED"):
        run(reviewed, lambda *a: pytest.fail("old unsafe send"), apply=True, clocks=clock)
    assert not adoption.paths(deployment.STORAGE, a[4])[1].exists()
    result = prepare_clock(reviewed, clock)
    assert (
        not result["startup_ready"]
        and result["shared_journal_changed"]
        and result["http_attempts"] == 0
    )
    assert (
        result["initial_clock_reason"]
        == dict(boot="BOOT_CHANGED", wall="WALL_MONOTONIC_DRIFT", backwards="MONOTONIC_BACKWARDS")[
            kind
        ]
    )
    after = common.resource_rows(a[1].engine, a[0].scope)[0]
    assert (
        len(after) == len(old) + 2
        and all(after[k] == v for k, v in old.items())
        and financial(a) == before
    )
    sha = result["preparation_snapshot_sha256"]
    status = clock_status(reviewed, clock, sha)
    assert not status["startup_ready"] and not status["shared_journal_changed"]
    with pytest.raises(common.ProbeBlocked, match="AUDIT_NOT_READY"):
        run(reviewed, lambda *a: pytest.fail("early send"), apply=True, clocks=clock)
    a[2].seconds += 70.002
    status = clock_status(reviewed, clock, sha)
    assert status["startup_ready"] and financial(a) == before
    assert common.resource_rows(a[1].engine, a[0].scope)[0] == after
    if kind == "boot":
        result = run(reviewed, day_sender(reviewed, []), apply=True, clocks=clock)
        assert result["day_adopted"] and result["application_http_attempts"] == 8
        assert run(reviewed, status=True, clocks=clock)["http_attempts"] == 0


def test_valid_clock_is_noop_and_restart_resumes_with_reads_only(reviewed):
    a = reviewed[0]
    old = common.resource_rows(a[1].engine, a[0].scope)[0]
    first = prepare_clock(reviewed)
    assert first["startup_ready"] and not first["shared_journal_changed"]
    folder = adoption.clock_folder(deployment.STORAGE, a[4])
    files = {p.name: p.read_bytes() for p in folder.iterdir()}
    again = prepare_clock(reviewed, credentials=lambda: pytest.fail("resumed key read"))
    assert again["startup_ready"] and not again["shared_journal_changed"]
    assert common.resource_rows(a[1].engine, a[0].scope)[0] == old
    assert {p.name: p.read_bytes() for p in folder.iterdir()} == files
    assert run(reviewed, day_sender(reviewed, []), apply=True)["day_adopted"]


def test_second_clock_change_is_not_automatically_repaired(reviewed):
    first = replace(reviewed[3], boot=lambda: "first-boot")
    prepare_clock(reviewed, first)
    old = common.resource_rows(reviewed[0][1].engine, reviewed[0][0].scope)[0]
    with pytest.raises(LocalQuotaStopped, match="SHARED_CLOCK_OR_BOOT_CHANGED"):
        prepare_clock(reviewed, replace(first, boot=lambda: "second-boot"))
    assert common.resource_rows(reviewed[0][1].engine, reviewed[0][0].scope)[0] == old


def test_missing_keys_does_not_create_preparation_or_rebase(reviewed):
    def missing():
        raise RuntimeError("PRIVATE_KEY_EXCEPTION")

    before = common.resource_rows(reviewed[0][1].engine, reviewed[0][0].scope)[0]
    with pytest.raises(common.ProbeBlocked, match="CREDENTIAL_SESSION_UNAVAILABLE"):
        prepare_clock(reviewed, replace(reviewed[3], boot=lambda: "new-boot"), credentials=missing)
    assert not adoption.clock_folder(deployment.STORAGE, reviewed[0][4]).exists()
    assert common.resource_rows(reviewed[0][1].engine, reviewed[0][0].scope)[0] == before


def test_changed_preview_blocks_before_backup_and_rebase(reviewed):
    _, folder = common.day13_paths(deployment.STORAGE, reviewed[0][4])
    body = folder / "complete.json"
    body.write_bytes(body.read_bytes().replace(b"4998.74183608", b"4998.74183609"))
    old = common.resource_rows(reviewed[0][1].engine, reviewed[0][0].scope)[0]
    with pytest.raises(common.ProbeBlocked, match="REVIEWED_VALUES_CHANGED"):
        prepare_clock(reviewed, replace(reviewed[3], boot=lambda: "new-boot"))
    assert not adoption.clock_folder(deployment.STORAGE, reviewed[0][4]).exists()
    assert common.resource_rows(reviewed[0][1].engine, reviewed[0][0].scope)[0] == old


def test_apply_intent_and_incomplete_preparation_are_never_reset(reviewed):
    folder = adoption.clock_folder(deployment.STORAGE, reviewed[0][4])
    folder.mkdir(mode=0o700)
    with pytest.raises(common.ProbeBlocked, match="PREPARATION_INCOMPLETE"):
        prepare_clock(reviewed, credentials=lambda: pytest.fail("key read"))
    app = adoption.paths(deployment.STORAGE, reviewed[0][4])[1]
    app.mkdir(mode=0o700)
    with pytest.raises(common.ProbeBlocked, match="ALREADY_ATTEMPTED"):
        prepare_clock(reviewed, credentials=lambda: pytest.fail("key read"))
