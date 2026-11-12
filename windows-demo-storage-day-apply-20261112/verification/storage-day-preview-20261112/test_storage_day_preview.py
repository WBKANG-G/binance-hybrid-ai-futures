"""Day-12 signed preview preserves configured/recovered runtime storage and prior receipts."""

# ruff: noqa: F811
import importlib.util
from dataclasses import replace
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "storage_fixture", BASE / "runtime-storage-setup-20261111/test_runtime_storage_setup.py"
)
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)
setup = previous.setup
closed = previous.closed
demo_system = previous.demo_system
applied = previous.applied
spec = importlib.util.spec_from_file_location(
    "storage_day_preview", Path(__file__).with_name("preview_storage_day_20261112.py")
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


@pytest.fixture
def configured(applied, monkeypatch):
    previous.prepare(applied)
    applied[4](70.002)
    previous.finalize(applied)
    monkeypatch.setattr(m, "ACCOUNT", "DEMO-test")
    monkeypatch.setattr(m.r, "ACCOUNT", "DEMO-test")
    applied[4](86400)
    store, _, _, _, _, original = applied
    old_client, old_monitor, options, calls, route = (
        original[1],
        original[2],
        original[3],
        original[4],
        original[7],
    )
    calls.clear()
    options.update(
        rate_at=None,
        response_fault=None,
        account_calls=0,
        income_calls=0,
        extra_trade=False,
        late_income=False,
    )
    monitor = m.Monitor(route, clock=old_monitor.clock)
    client = m.DayPreviewClient(
        old_client.settings, old_client.credentials, send=monitor, clock_ms=old_client.clock_ms
    )
    monitor.server_ms = lambda: client.clock_ms() + client.offset
    yield applied, client, monitor, options, calls
    route.close()


def run(a, **kwargs):
    setup, client, monitor, *_ = a
    return m.storage_preview(
        setup[0],
        setup[1],
        client,
        monitor,
        parent=setup[2],
        clocks=setup[3],
        attest=True,
        attested_day="2026-11-12",
        **kwargs,
    )


def test_signed_day12_preview_preserves_all_financial_storage_and_checkpoint_state(configured):
    a, _, _, _, calls = configured
    rows = previous.rows(a[0])
    files = previous.disk(a[2])
    anchors = m.checkpoint_token(a[1].engine)
    result = run(configured)
    assert result["old_day_kst"] == "2026-11-11" and result["proposed_day_kst"] == "2026-11-12"
    assert result["old_week_kst"] == result["proposed_week_kst"] == "2026-11-09"
    assert result["proposed_daily_base_usdt"] == "4998.74183608"
    assert result["day_boundary_utc"] == "2026-11-11T15:00:00+00:00"
    assert (
        result["prior_attested_day_receipt_verified"] and result["storage_configuration_verified"]
    )
    assert result["storage_receipts_preserved"] and result["shared_checkpoint_history_preserved"]
    assert not result["baseline_adoption_approved"] and not result["database_changed_by_preview"]
    assert result["http_attempts"] == len(calls) == 23
    assert all(call[0] == "GET" for call in calls)
    assert previous.rows(a[0]) == rows and previous.disk(a[2]) == files
    assert m.checkpoint_token(a[1].engine) == anchors


@pytest.mark.parametrize(
    "fault",
    ["open_regular", "open_algo", "hedge_mode", "full_trades", "non_usdt_fee", "late_second_pass"],
)
def test_changed_or_incomplete_exchange_evidence_never_approves_or_changes_storage(
    configured, fault
):
    a, _, _, options, _ = configured
    options["response_fault"] = fault
    before = previous.rows(a[0])
    files = previous.disk(a[2])
    anchors = m.checkpoint_token(a[1].engine)
    with pytest.raises(m.Blocked):
        run(configured)
    assert previous.rows(a[0]) == before and previous.disk(a[2]) == files
    assert m.checkpoint_token(a[1].engine) == anchors


def test_rate_limit_stops_without_retry(configured):
    a, _, _, options, calls = configured
    options["rate_at"] = "/fapi/v1/accountConfig"
    with pytest.raises(m.DemoRateLimited):
        run(configured)
    assert sum(call[1] == "/fapi/v1/accountConfig" for call in calls) == 1


def test_missing_attestation_and_wrong_day_do_not_send_requests(configured):
    a, client, monitor, _, calls = configured
    for attest, day, code in [
        (False, "2026-11-12", "OPERATOR_ATTESTATION_REQUIRED"),
        (True, "2026-11-13", "CURRENT_DAY_ATTESTATION_REQUIRED"),
    ]:
        with pytest.raises(m.Blocked, match=code):
            m.storage_preview(
                a[0],
                a[1],
                client,
                monitor,
                parent=a[2],
                clocks=a[3],
                attest=attest,
                attested_day=day,
            )
    assert not calls


def test_shared_clock_fault_refuses_before_exchange_reads(configured):
    a, client, monitor, _, calls = configured
    clocks = replace(a[3], boot=lambda: "00000000-0000-0000-0000-000000000002")
    with pytest.raises(Exception, match="SHARED_CLOCK_OR_BOOT_CHANGED"):
        m.storage_preview(
            a[0],
            a[1],
            client,
            monitor,
            parent=a[2],
            clocks=clocks,
            attest=True,
            attested_day="2026-11-12",
        )
    assert not calls


def test_checkpoint_change_during_preview_invalidates_report(configured, monkeypatch):
    original = m.checkpoint_token
    count = [0]

    def changed(engine):
        count[0] += 1
        return original(engine) if count[0] == 1 else "0" * 64

    monkeypatch.setattr(m, "checkpoint_token", changed)
    with pytest.raises(m.Blocked, match="CHECKPOINT_CHANGED_DURING_PREVIEW"):
        run(configured)


def test_storage_change_during_preview_invalidates_report(configured, monkeypatch):
    original = m.r.readonly_files
    count = [0]

    def changed(root):
        count[0] += 1
        return original(root) if count[0] == 1 else original(root) | {"synthetic_change": "0" * 64}

    monkeypatch.setattr(m.r, "readonly_files", changed)
    with pytest.raises(m.Blocked, match="STORAGE_CHANGED_DURING_PREVIEW"):
        run(configured)


def test_prior_financial_receipt_cannot_be_skipped(configured, monkeypatch):
    monkeypatch.setattr(m.r, "day_recorded", lambda ledger: False)
    with pytest.raises(m.r.Blocked, match="SETUP_DAY_RECEIPT_REQUIRED"):
        run(configured)
    assert not configured[4]


def test_actual_clock_recovery_backup_and_checkpoint_survive_signed_preview(
    configured, monkeypatch
):
    spec = importlib.util.spec_from_file_location(
        "unused_clock_recovery",
        BASE / "runtime-clock-recovery-20261112/recover_unused_shared_clock.py",
    )
    recovery = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(recovery)
    monkeypatch.setattr(recovery.r, "ACCOUNT", "DEMO-test")
    a, client, monitor, _, calls = configured
    clocks = replace(a[3], boot=lambda: "00000000-0000-0000-0000-000000000002")
    result = recovery.recover(a[0], a[1], parent=a[2], clocks=clocks)
    assert result["runtime_clock_recovery"] == "WAIT_STARTUP_HOLD"
    a[4](70.002)
    rows = previous.rows(a[0])
    files = previous.disk(a[2])
    anchors = m.checkpoint_token(a[1].engine)
    assert any("clock-recovery-" in name and name.endswith("original.sqlite3") for name in files)
    report = m.storage_preview(
        a[0],
        a[1],
        client,
        monitor,
        parent=a[2],
        clocks=clocks,
        attest=True,
        attested_day="2026-11-12",
    )
    assert report["clock_recovery_files_preserved"] and report["storage_configuration_verified"]
    assert previous.rows(a[0]) == rows and previous.disk(a[2]) == files
    assert m.checkpoint_token(a[1].engine) == anchors
