"""Installed audit clock recovery: preserve history, no requests until full hold."""

# ruff: noqa: F401, F811
import json
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import select, update

from integrations.binance_usdm.rest import RESTResponse
from packages.persistence.demo import accounts
from packages.persistence.demo_order_quota import LocalQuotaStopped
from scripts import probe_demo_accounted_reads as probe
from scripts import review_demo_read_code_install as fingerprint
from scripts import review_demo_read_deployment as deployment
from tests.integration.test_accounted_read_probe import (
    anchored,
    candidate,
    demo_system,
    installation,
    invoke,
    marker,
    ready,
    sender,
)
from tests.integration.test_runtime_read_accounting import financial


def recover(case, clock=None, **kwargs):
    a = case[0]
    return probe.recover_probe_clock(
        a[1].engine,
        deployment.STORAGE,
        a[0].scope,
        a[4],
        a[1].installation_id(),
        "DEMO-test",
        fingerprint,
        clocks=clock or replace(case[3], boot=lambda: "changed-boot"),
        **kwargs,
    )


@pytest.mark.parametrize("kind", ["boot", "wall", "backwards"])
def test_changed_clock_recovery_then_full_hold_then_exactly_two_gets(installation, kind):
    a = installation[0]
    clock = dict(
        boot=replace(installation[3], boot=lambda: "changed-boot"),
        wall=replace(installation[3], wall_clock=lambda: a[2].utc() + timedelta(seconds=5)),
        backwards=replace(installation[3], monotonic=lambda: a[2].mono() - 50),
    )[kind]
    before = financial(a)
    resources, _ = probe.resource_rows(a[1].engine, a[0].scope)
    original = deployment.STORAGE.joinpath(a[0].path.name).read_bytes()
    with pytest.raises(LocalQuotaStopped, match="SHARED_CLOCK_OR_BOOT_CHANGED"):
        invoke(installation, lambda *args: pytest.fail("premature wire"), clocks=clock)
    assert not marker(installation).exists()
    result = recover(installation, clock, apply=True)
    assert (
        not result["startup_ready"]
        and result["shared_journal_changed"]
        and result["http_attempts"] == 0
    )
    assert result["audit_clock_event_added"] and result["resource_checkpoint_added"]
    assert result["financial_ledger_unchanged_verified"] and financial(a) == before
    after, actions = probe.resource_rows(a[1].engine, a[0].scope)
    assert all(after[k] == v for k, v in resources.items()) and len(after) == len(resources) + 2
    assert actions[max(actions)]["kind"] == "CLOCK_REBASE"
    backup = next(deployment.STORAGE.glob("read-clock-recovery-*/original.sqlite3"))
    assert backup.read_bytes() == original
    expected = result["recovery_post_snapshot_sha256"]
    poll = recover(installation, clock, expected=expected)
    assert not poll["startup_ready"] and not poll["shared_journal_changed"]
    assert probe.resource_rows(a[1].engine, a[0].scope)[0] == after
    with pytest.raises(LocalQuotaStopped, match="READ_SETUP_STARTUP_HOLD"):
        invoke(installation, lambda *args: pytest.fail("hold wire"), clocks=clock)
    a[2].seconds += 70.002
    poll = recover(installation, clock, expected=expected)
    assert poll["startup_ready"] and not poll["shared_journal_changed"]
    assert probe.resource_rows(a[1].engine, a[0].scope)[0] == after
    seen = []
    good = sender(installation, seen)

    def matching_clock(*args):
        response = good(*args)
        if args[1].endswith("/time"):
            return RESTResponse(
                200,
                json.dumps(dict(serverTime=int(clock.wall_clock().timestamp() * 1000))).encode(),
                response.headers,
            )
        return response

    final = invoke(installation, matching_clock, clocks=clock)
    assert len(seen) == 2 and final["modeled_weight_charged"] == 6
    assert financial(a) == before and backup.read_bytes() == original
    with pytest.raises(probe.ProbeBlocked, match="ALREADY_ATTEMPTED"):
        recover(installation, clock, apply=True)


def test_clock_apply_requires_explicit_choice_before_access():
    with pytest.raises(probe.ProbeBlocked, match="EXPLICIT_OPERATION"):
        probe.recover_probe_clock(None, None, None, None, None, None, None)


def test_good_clock_noop_does_not_append_or_backup(installation):
    before = probe.resource_rows(installation[0][1].engine, installation[0][0].scope)
    result = recover(installation, installation[3], apply=True)
    assert result["startup_ready"] and not result["shared_journal_changed"]
    assert not list(deployment.STORAGE.glob("read-clock-recovery-*"))
    assert probe.resource_rows(installation[0][1].engine, installation[0][0].scope) == before


def test_status_does_not_repair_another_clock_change(installation):
    result = recover(installation, apply=True)
    before = probe.resource_rows(installation[0][1].engine, installation[0][0].scope)
    with pytest.raises(LocalQuotaStopped, match="SHARED_CLOCK_OR_BOOT_CHANGED"):
        recover(
            installation,
            replace(installation[3], boot=lambda: "third-boot"),
            expected=result["recovery_post_snapshot_sha256"],
        )
    assert probe.resource_rows(installation[0][1].engine, installation[0][0].scope) == before


def test_status_detects_financial_change(installation):
    result = recover(installation, apply=True)
    with installation[0][1].engine.begin() as c:
        value = c.execute(select(accounts.c.state)).scalar_one()
        value["high_water"] = "777"
        c.execute(update(accounts).values(state=value))
    with pytest.raises(probe.ProbeBlocked, match="PROTECTED_STATE_CHANGED"):
        recover(installation, expected=result["recovery_post_snapshot_sha256"])


def test_used_or_pending_scope_is_not_reset_even_if_intent_is_missing(installation):
    def unknown(*args):
        raise OSError("synthetic lost response")

    with pytest.raises(LocalQuotaStopped):
        invoke(installation, unknown)
    # Synthetic test simulates a missing outer marker. The actual audit still blocks recovery.
    for p in marker(installation).iterdir():
        p.unlink()
    marker(installation).rmdir()
    before = probe.resource_rows(installation[0][1].engine, installation[0][0].scope)
    with pytest.raises(probe.ProbeBlocked, match="UNUSED_SCOPE_REQUIRED"):
        recover(installation, apply=True)
    assert probe.resource_rows(installation[0][1].engine, installation[0][0].scope) == before
    assert not list(deployment.STORAGE.glob("read-clock-recovery-*"))
