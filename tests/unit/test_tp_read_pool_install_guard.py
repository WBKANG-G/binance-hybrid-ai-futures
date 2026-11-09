import copy
import hashlib
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("fix_guard", Path("guard_tp_read_pool_install.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
SAFE = {
    "installation_review": "NORMAL_PAUSED_FULL_READ_ONLY_AUDIT",
    "period_keys_match": True,
    "audited_request_count": 91,
    "signed_query_count": 350,
    "risk_gates": "PASS",
    "position_nonzero": False,
    "active_orders": 0,
    "paused": True,
    "risk_state": "NORMAL",
    "orders_enabled": False,
    "live_trading_enabled": False,
    "unresolved_count": 4,
    "review_token": "SYNTHETIC_PRIVATE_TOKEN",
}


@pytest.fixture
def frozen_source_tree(tmp_path, monkeypatch):
    """Exercise the old guard against a pinned fixture, not a newer checkout.

    Production pins remain immutable. Otherwise unrelated runtime development
    makes both safe and unsafe review tests stop at the same hash check, hiding
    whether the actual review predicates still work.
    """
    pins = {}
    for name in m.UNCHANGED:
        body = ("SYNTHETIC_FROZEN_SOURCE:" + name).encode()
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        pins[name] = hashlib.sha256(body).hexdigest()
    pool = tmp_path / "integrations/binance_usdm/demo_read_routing.py"
    pool.write_bytes(b"SYNTHETIC_FROZEN_POOL")
    monkeypatch.setattr(m, "UNCHANGED", pins)
    monkeypatch.setattr(m, "POOL_SHA", hashlib.sha256(pool.read_bytes()).hexdigest())
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.mark.parametrize(
    "phase,digest", [("pre", m.OLD_SHA), ("pre", m.NEW_SHA), ("post", m.NEW_SHA)]
)
def test_only_read_review_is_called_and_token_never_returned(phase, digest, frozen_source_tree):
    calls = []

    def local(engine):
        calls.append("LOCAL")
        return ("STABLE",)

    def review():
        calls.append("REVIEW_ONLY")
        return copy.deepcopy(SAFE)

    report = m.run(None, phase, read_local=local, read_review=review, digest=digest)
    assert calls == ["LOCAL", "REVIEW_ONLY", "LOCAL"]
    assert report["paused"] and not report["recovery_applied"] and not report["database_changed"]
    assert "SYNTHETIC_PRIVATE_TOKEN" not in str(report)


@pytest.mark.parametrize(
    "change",
    [
        {"risk_gates": "FAIL"},
        {"position_nonzero": True},
        {"active_orders": 1},
        {"paused": False},
        {"orders_enabled": True},
        {"live_trading_enabled": True},
        {"installation_review": "APPLIED"},
        {"period_keys_match": False},
        {"risk_state": "EMERGENCY"},
        {"unresolved_count": True},
    ],
)
def test_unsafe_review_blocks_without_apply(change, frozen_source_tree):
    with pytest.raises(m.GuardBlocked, match="INSTALL_GUARD_BLOCKED"):
        m.run(
            None,
            "pre",
            read_local=lambda e: ("STABLE",),
            read_review=lambda: SAFE | change,
            digest=m.OLD_SHA,
        )


def test_changed_account_blocks_after_review(frozen_source_tree):
    rows = iter([("BEFORE",), ("CHANGED",)])
    with pytest.raises(m.GuardBlocked, match="ACCOUNT_CHANGED_DURING_REVIEW"):
        m.run(
            None, "pre", read_local=lambda e: next(rows), read_review=lambda: SAFE, digest=m.OLD_SHA
        )


def test_post_update_requires_new_runtime_source():
    with pytest.raises(ValueError):
        m.run(
            None, "post", digest=m.OLD_SHA, read_local=lambda e: pytest.fail("no DB for bad source")
        )


def test_cli_raw_failure_is_redacted(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["guard", "--phase", "pre"])

    def fail(*a, **k):
        raise ValueError("PRIVATE_CONNECTION_OR_TOKEN")

    monkeypatch.setattr(m, "create_engine", fail)
    with pytest.raises(SystemExit) as error:
        m.main()
    assert error.value.code == 2 and "PRIVATE_CONNECTION_OR_TOKEN" not in capsys.readouterr().out


def test_default_review_uses_real_normal_audit_without_recovery_cli(monkeypatch):
    engine, ledger, client = object(), object(), object()
    store = object()
    monkeypatch.setattr(m, "DemoStore", lambda e: store if e is engine else None)
    calls = []

    def audit(s, c, state):
        calls.append((s, c, state))
        return SAFE

    monkeypatch.setattr(m, "normal_audit", audit)
    assert m.review(engine, ledger, client) == SAFE
    assert calls == [(store, client, ledger)]


def test_production_unresolved_refs_match_original_independent_report():
    import json

    original = json.loads(Path("TP_READ_POOL_REFERENCE.json").read_text())
    assert (
        original["unresolved_set_digest"]
        == "1801ba7f74cf0ac87804b251bb1645498d278b3f008705579b27f682c6ee8de4"
    )
    assert m.UNRESOLVED_REFS == set(original["request_refs"])
    assert len(m.UNRESOLVED_REFS) == 4


@pytest.mark.parametrize("changed", ["unchanged", "pool"])
def test_tampered_pinned_fixture_stops_before_database(frozen_source_tree, changed):
    name = (
        next(iter(m.UNCHANGED))
        if changed == "unchanged"
        else "integrations/binance_usdm/demo_read_routing.py"
    )
    (frozen_source_tree / name).write_bytes(b"SYNTHETIC_TAMPER")
    with pytest.raises(m.GuardBlocked, match="SOURCE_HASH_MISMATCH"):
        m.run(
            None,
            "post",
            digest=m.NEW_SHA,
            read_local=lambda e: pytest.fail("No database access for changed source"),
        )
