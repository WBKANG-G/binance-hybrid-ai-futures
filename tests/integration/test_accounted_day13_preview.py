"""A narrow prior-day preview preserves every financial row and authentic audit history."""

# ruff: noqa: F401, F811
import json
from dataclasses import replace
from urllib.parse import parse_qsl

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
    ready,
    sender,
)
from tests.integration.test_runtime_read_accounting import financial


@pytest.fixture
def day12(installation):
    a = installation[0]
    a[2].seconds += 3 * 86400
    with a[1].engine.begin() as c:
        value = c.execute(select(accounts.c.state)).scalar_one()
        value.update(
            daily_key="2026-11-12",
            weekly_key="2026-11-09",
            daily_base="4998.74183608",
            weekly_base="4998.74183608",
            high_water="5000",
        )
        value["value"].update(initial="4998.74183608", as_of="2026-11-06T07:25:25.109073+00:00")
        c.execute(update(accounts).values(state=value))
    invoke(installation, sender(installation, []))
    a[2].seconds += 86400
    return installation


def prepare(case, clock=None, **kwargs):
    a = case[0]
    return probe.recover_probe_clock(
        a[1].engine,
        deployment.STORAGE,
        a[0].scope,
        a[4],
        a[1].installation_id(),
        "DEMO-test",
        fingerprint,
        clocks=clock or case[3],
        day13=True,
        **kwargs,
    )


def wallet():
    w = "4998.74183608"
    return dict(
        assets=[dict(asset="USDT", walletBalance=w, marginBalance=w, unrealizedProfit="0")],
        totalWalletBalance=w,
        totalMarginBalance=w,
        totalUnrealizedProfit="0",
        totalInitialMargin="0",
        availableBalance=w,
    )


def day_sender(case, seen, change=None):
    total = 6

    def send(method, path, query, key):
        nonlocal total
        seen.append(path)
        actions = probe.resource_rows(case[0][1].engine, case[0][0].scope)[1]
        assert actions[max(actions)]["kind"] == "RESERVE"
        assert actions[max(actions)]["path"] == path
        total += dict(probe.DAY13_ROUTES)[path]
        if path.endswith("/time"):
            body = dict(serverTime=int(case[0][2].utc().timestamp() * 1000))
        elif path.endswith("/accountConfig"):
            body = dict(dualSidePosition=False, multiAssetsMargin=False)
        elif path.endswith("/account"):
            body = wallet()
        else:
            body = []
        if path.endswith("/income"):
            params = dict(parse_qsl(query.get_secret_value()))
            assert (
                params["startTime"] == "1794409200000"
                and params["limit"] == "1000"
                and params["page"] == "1"
            )
            assert int(params["endTime"]) == int(case[0][2].utc().timestamp() * 1000)
        if change is not None:
            body = change(path, body)
        return RESTResponse(200, json.dumps(body).encode(), {"x-mbx-used-weight-1m": str(total)})

    return send


def test_day13_preview_does_not_roll_date_or_modify_financial_history(day12):
    before = financial(day12[0])
    proof = prepare(day12, apply=True)
    assert proof["startup_ready"] and not proof["shared_journal_changed"]
    seen = []
    result = invoke(day12, day_sender(day12, seen), day13_preview=True)
    assert seen == [p for p, _ in probe.DAY13_ROUTES]
    assert (
        result["http_attempts"] == result["completed_reads"] == 8
        and result["modeled_weight_charged"] == 131
    )
    assert result["audit_events_added"] == result["resource_checkpoints_added"] == 16
    assert result["accounted_read_probe"] == "DAY13_PREVIEW_NO_FINANCIAL_CHANGE"
    assert result["proposed_day_kst"] == "2026-11-13" and result["old_day_kst"] == "2026-11-12"
    assert (
        not result["day_adopted"]
        and not result["baseline_adoption_approved"]
        and not result["financial_database_changed_by_preview"]
    )
    assert (
        not result["exact_day_midnight_valuation_verified"]
        and not result["account_wide_trade_export_verified"]
    )
    assert result["wallet_rechecked"] and result["income_since_prior_day_empty"]
    assert financial(day12[0]) == before
    with pytest.raises(probe.ProbeBlocked, match="ALREADY_ATTEMPTED"):
        invoke(day12, lambda *a: pytest.fail("repeat"), day13_preview=True)


def test_changed_boot_preserves_completed_charge_and_waits_before_preview(day12):
    clock = replace(day12[3], boot=lambda: "new-day13-boot")
    before = financial(day12[0])
    result = prepare(day12, clock, apply=True)
    assert result["shared_journal_changed"] and not result["startup_ready"]
    assert not result["http_attempts"]
    after = probe.resource_rows(day12[0][1].engine, day12[0][0].scope)[0]
    result = prepare(day12, clock, expected=result["recovery_post_snapshot_sha256"])
    assert not result["startup_ready"] and not result["shared_journal_changed"]
    assert probe.resource_rows(day12[0][1].engine, day12[0][0].scope)[0] == after
    day12[0][2].seconds += 70.002
    result = prepare(day12, clock, expected=result["recovery_post_snapshot_sha256"])
    assert result["startup_ready"] and financial(day12[0]) == before
    final = invoke(day12, day_sender(day12, []), day13_preview=True, clocks=clock)
    assert final["modeled_weight_charged"] == 131


@pytest.mark.parametrize("kind", ["wallet", "income", "position", "regular", "algo", "last_wallet"])
def test_changed_signed_evidence_stops_without_financial_application(day12, kind):
    before = financial(day12[0])
    prepare(day12, apply=True)
    n = 0

    def change(path, body):
        nonlocal n
        if path.endswith("/account"):
            n += 1
            if kind == "wallet" or (kind == "last_wallet" and n == 2):
                body["totalWalletBalance"] = "4999"
        if kind == "income" and path.endswith("/income"):
            return [dict(income="0")]
        if kind == "position" and path.endswith("/positionRisk"):
            return [dict(symbol="ETHUSDT", positionSide="BOTH", positionAmt="1")]
        if kind == "regular" and path.endswith("/openOrders"):
            return [dict(orderId=1)]
        if kind == "algo" and path.endswith("/openAlgoOrders"):
            return [dict(algoId=1)]
        return body

    with pytest.raises(probe.ProbeBlocked):
        invoke(day12, day_sender(day12, [], change), day13_preview=True)
    assert financial(day12[0]) == before
    marker = probe.day13_paths(deployment.STORAGE, day12[0][4])[1]
    assert marker.joinpath("intent.json").exists() and not marker.joinpath("complete.json").exists()


def test_preview_does_not_bypass_missing_preparation(day12):
    with pytest.raises(OSError):
        invoke(day12, lambda *a: pytest.fail("wire"), day13_preview=True)


def test_new_day_attestation_does_not_extend_to_day14(day12):
    day12[0][2].seconds += 86400
    with pytest.raises(probe.ProbeBlocked, match="CURRENT_DAY_REQUIRED"):
        prepare(day12, apply=True)


def test_preparation_witness_detects_intervening_state_change(day12):
    prepare(day12, apply=True)
    path = deployment.STORAGE.parent / "unexpected-receipt"
    path.write_bytes(b"changed")
    path.chmod(0o600)
    with pytest.raises(probe.ProbeBlocked, match="PROTECTED_STATE_CHANGED"):
        invoke(day12, lambda *a: pytest.fail("wire"), day13_preview=True)
