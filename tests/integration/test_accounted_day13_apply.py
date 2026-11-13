"""Atomic operator-attested period adoption with durable GETs and independent status."""

# ruff: noqa: F401, F811
import json

import pytest
from sqlalchemy import event, select, text

from packages.persistence.demo import accounts, events, outbox
from packages.persistence.demo_order_quota import LocalQuotaStopped
from scripts import adopt_demo_accounted_day13 as adoption
from scripts import probe_demo_accounted_reads as common
from scripts import review_demo_read_code_install as fingerprint
from scripts import review_demo_read_deployment as deployment
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
from tests.integration.test_runtime_read_accounting import financial, settings


@pytest.fixture
def reviewed(day12):
    prepare(day12, apply=True)
    result = invoke(day12, day_sender(day12, []), day13_preview=True)
    assert result["modeled_weight_charged"] == 131
    return day12


def run(case, send=None, **kw):
    a = case[0]
    return adoption.run(
        a[1].engine,
        deployment.STORAGE,
        a[0].scope,
        a[4],
        a[1].installation_id(),
        "DEMO-test",
        settings(a),
        kw.pop("credentials", lambda: a[7].client.credentials),
        fingerprint,
        common,
        clocks=kw.pop("clocks", case[3]),
        sender=send,
        **kw,
    )


def test_atomic_adoption_preserves_prior_records_and_read_only_repeat_is_noop(reviewed):
    a = reviewed[0]
    before = financial(a)
    prior, _ = common.resource_rows(a[1].engine, a[0].scope)
    seen = []
    result = run(reviewed, day_sender(reviewed, seen), apply=True)
    assert (
        len(seen) == 8
        and result["http_attempts"] == 8
        and result["application_modeled_weight_charged"] == 131
    )
    assert result["day_adopted"] and result["day_kst"] == "2026-11-13"
    assert (
        result["prior_financial_history_preserved"]
        and result["only_reviewed_account_fields_changed"]
    )
    assert (
        not result["orders_enabled"]
        and not result["execution_approved"]
        and not result["live_trading_enabled"]
    )
    assert (
        not result["exact_day_midnight_valuation_verified"]
        and not result["account_wide_trade_export_verified"]
    )
    after = financial(a)
    assert len(after[1]) == len(before[1]) + 1 and len(after[2]) == len(before[2]) + 1
    operation, folder = adoption.paths(deployment.STORAGE, a[4])
    with a[1].engine.connect() as c:
        old = dict(before[0][0]._mapping)
        new = dict(c.execute(select(accounts)).mappings().one())
        assert new["fence"] == old["fence"] + 1 and new["owner"] == old["owner"] is None
        new_state = new["state"]
        assert new_state["daily_key"] == "2026-11-13"
        assert (
            operation in new_state["attested_day_baselines"]
            and operation in new_state["operations"]
        )
        new_state["daily_key"] = "2026-11-12"
        del new_state["operations"][operation]
        del new_state["attested_day_baselines"][operation]
        assert new_state == old["state"]
        assert all(r in after[1] for r in before[1]) and all(r in after[2] for r in before[2])
    assert {p.name for p in folder.iterdir()} == {
        "intent.json",
        "commit-intent.json",
        "complete.json",
    }
    assert folder.stat().st_mode & 0o777 == 0o700
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in folder.iterdir())
    post, _ = common.resource_rows(a[1].engine, a[0].scope)
    assert len(post) == len(prior) + 32 and all(post[k] == v for k, v in prior.items())
    snapshot = financial(a)
    status = run(reviewed, status=True, credentials=lambda: pytest.fail("status key access"))
    assert status["http_attempts"] == 0 and status["application_http_attempts"] == 8
    assert financial(a) == snapshot and common.resource_rows(a[1].engine, a[0].scope)[0] == post
    with pytest.raises(common.ProbeBlocked, match="ALREADY_ATTEMPTED_RUN_STATUS"):
        run(
            reviewed,
            lambda *a: pytest.fail("repeat send"),
            apply=True,
            credentials=lambda: pytest.fail("repeat keys"),
        )


def test_missing_keys_have_no_financial_effect(reviewed):
    before = financial(reviewed[0])

    def missing():
        raise RuntimeError("SECRET_IS_PRIVATE")

    with pytest.raises(common.ProbeBlocked, match="CREDENTIAL_SESSION_UNAVAILABLE"):
        run(reviewed, apply=True, credentials=missing)
    assert financial(reviewed[0]) == before
    assert not adoption.paths(deployment.STORAGE, reviewed[0][4])[1].exists()


@pytest.mark.parametrize("kind", ["wallet", "income", "position", "regular", "algo", "unknown"])
def test_bad_fresh_evidence_leaves_old_period_and_durable_cost(reviewed, kind):
    before = financial(reviewed[0])

    def change(path, body):
        if kind == "wallet" and path.endswith("/account"):
            body["totalWalletBalance"] = "4999"
        if kind == "income" and path.endswith("/income"):
            return [{"income": "0"}]
        if kind == "position" and path.endswith("/positionRisk"):
            return [dict(symbol="ETHUSDT", positionSide="BOTH", positionAmt="1")]
        if kind == "regular" and path.endswith("/openOrders"):
            return [{"orderId": 1}]
        if kind == "algo" and path.endswith("/openAlgoOrders"):
            return [{"algoId": 1}]
        return body

    good = day_sender(reviewed, [], change)

    def wire(*args):
        if kind == "unknown" and args[1].endswith("/positionRisk"):
            raise OSError("PRIVATE_SYNTHETIC_NETWORK")
        return good(*args)

    state = {}
    with pytest.raises((common.ProbeBlocked, LocalQuotaStopped)):
        run(reviewed, wire, apply=True, state=state)
    assert financial(reviewed[0]) == before and not state.get(
        "application_may_have_completed", False
    )
    folder = adoption.paths(deployment.STORAGE, reviewed[0][4])[1]
    assert folder.joinpath("intent.json").exists() and not folder.joinpath("complete.json").exists()
    with pytest.raises(common.ProbeBlocked, match="ALREADY_ATTEMPTED_RUN_STATUS"):
        run(reviewed, apply=True)


def test_triggered_unreviewed_account_change_rolls_back_all_financial_writes(reviewed):
    a = reviewed[0]
    before = financial(a)
    with a[1].engine.begin() as c:
        c.execute(
            text(
                "CREATE FUNCTION corrupt_adoption() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN NEW.state=jsonb_set(NEW.state,'{high_water}', '\"5001\"'::jsonb); RETURN NEW; END $$"
            )
        )
        c.execute(
            text(
                "CREATE TRIGGER corrupt_adoption BEFORE UPDATE ON demo_accounts FOR EACH ROW EXECUTE FUNCTION corrupt_adoption()"
            )
        )
    state = {}
    with pytest.raises(common.ProbeBlocked, match="UNREVIEWED_ACCOUNT_CHANGE"):
        run(reviewed, day_sender(reviewed, []), apply=True, state=state)
    assert financial(a) == before
    folder = adoption.paths(deployment.STORAGE, a[4])[1]
    assert (
        folder.joinpath("commit-intent.json").exists()
        and not folder.joinpath("complete.json").exists()
    )
    with pytest.raises(common.ProbeBlocked):
        run(reviewed, status=True)


def test_commit_ack_loss_is_resolved_by_status_without_replaying_gets(reviewed, monkeypatch):
    engine = reviewed[0][1].engine
    targets = set()

    def mark(c, cursor, statement, parameters, context, many):
        if statement.startswith("UPDATE demo_accounts"):
            targets.add(id(c.connection.driver_connection))

    event.listen(engine, "before_cursor_execute", mark)
    real = engine.dialect.do_commit

    def lost_ack(raw):
        real(raw)
        identity = id(getattr(raw, "driver_connection", raw))
        if identity in targets:
            targets.remove(identity)
            raise OSError("SYNTHETIC_COMMIT_ACK_LOST")

    monkeypatch.setattr(engine.dialect, "do_commit", lost_ack)
    state = {}
    seen = []
    with pytest.raises(OSError, match="COMMIT_ACK_LOST"):
        run(reviewed, day_sender(reviewed, seen), apply=True, state=state)
    assert len(seen) == 8 and state["application_may_have_completed"]
    event.remove(engine, "before_cursor_execute", mark)
    monkeypatch.setattr(engine.dialect, "do_commit", real)
    status = run(reviewed, status=True, credentials=lambda: pytest.fail("status key access"))
    assert status["day_adopted"] and status["http_attempts"] == 0
    with pytest.raises(common.ProbeBlocked, match="ALREADY_ATTEMPTED"):
        run(reviewed, apply=True)
    assert len(seen) == 8


def test_postcommit_receipt_tampering_is_detected_against_private_commit_witness(reviewed):
    run(reviewed, day_sender(reviewed, []), apply=True)
    operation, _ = adoption.paths(deployment.STORAGE, reviewed[0][4])
    with reviewed[0][1].engine.begin() as c:
        raw = c.execute(select(accounts.c.state)).scalar_one()
        raw["attested_day_baselines"][operation]["baseline_usdt"] = "4999"
        c.execute(accounts.update().values(state=raw))
    with pytest.raises(common.ProbeBlocked, match="COMMIT_STATE_CHANGED"):
        run(reviewed, status=True)


def test_evidence_expiring_during_precommit_check_rolls_back(reviewed, monkeypatch):
    before = financial(reviewed[0])
    real = adoption.financial_witness

    def delayed(c, old, op, expected, fence, context):
        value = real(c, old, op, expected, fence, context)
        if fence != old["fence"]:
            reviewed[0][2].seconds += 31
        return value

    monkeypatch.setattr(adoption, "financial_witness", delayed)
    with pytest.raises(common.ProbeBlocked, match="EVIDENCE_STALE"):
        run(reviewed, day_sender(reviewed, []), apply=True)
    assert financial(reviewed[0]) == before


def test_day13_authorization_is_not_extended_to_day14(reviewed):
    before = financial(reviewed[0])
    reviewed[0][2].seconds += 86400
    with pytest.raises(common.ProbeBlocked, match="CURRENT_DAY_REQUIRED"):
        run(reviewed, lambda *a: pytest.fail("wire"), apply=True)
    assert financial(reviewed[0]) == before
    assert not adoption.paths(deployment.STORAGE, reviewed[0][4])[1].exists()


def test_missing_completed_preview_blocks_before_new_requests(day12):
    before = financial(day12[0])
    with pytest.raises(OSError):
        run(day12, lambda *a: pytest.fail("wire"), apply=True)
    assert financial(day12[0]) == before
    assert not adoption.paths(deployment.STORAGE, day12[0][4])[1].exists()
