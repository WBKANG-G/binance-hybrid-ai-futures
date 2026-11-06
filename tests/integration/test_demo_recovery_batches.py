"""31 historical requests are all audited; no old record is pruned to unlock."""

import json
from datetime import timedelta
from urllib.parse import parse_qs
from uuid import uuid4

import pytest

from integrations.binance_usdm.demo import DemoUnknown
from services.execution.demo_period_review import apply_periods, review_periods
from services.execution.demo_recovery import RecoveryBlocked, acknowledge_flat_emergency, review
from services.risk.paper import RiskRejected
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_cancelled_correction import correction  # noqa: F401
from tests.integration.test_demo_day_reconstruction import day_case  # noqa: F401
from tests.integration.test_demo_period_review import period_case  # noqa: F401


@pytest.fixture
def history_case(period_case):  # noqa: F811
    store, c, fake, *_ = period_case
    report = review_periods(
        store, c.client, store.read("DEMO-test"), account_id="DEMO-test", attest_no_activity=True
    )
    apply_periods(
        store,
        c.lease,
        c.client,
        uuid4(),
        report["review_token"],
        attest_no_activity=True,
        accept_operator_evidence=True,
    )
    state = store.read("DEMO-test")
    base = next(o for o in state.orders.values() if o.filled)
    entries = {}
    orders = {}
    algos = {}
    requests = {}
    for index in range(6):
        clone = base.model_copy(deep=True)
        clone.signal = base.signal.model_copy(update={"signal_id": uuid4()})
        for field, kind in [
            ("client_id", "DE"),
            ("exit_id", "DX"),
            ("algo_id", "DS"),
            ("tp_algo_id", "DT"),
        ]:
            original = getattr(base, field)
            identifier = f"{kind}-wide-{index:02}"
            setattr(clone, field, identifier)
            if kind in {"DE", "DX"}:
                raw = fake.orders[original].copy()
                raw["clientOrderId"] = identifier
                raw["orderId"] = str(10000 + index * 4 + (kind == "DX"))
                orders[identifier] = raw
                if kind == "DE":
                    clone.server_order_id = raw["orderId"]
            else:
                raw = fake.algos[original].copy()
                raw["clientAlgoId"] = identifier
                raw["algoId"] = str(20000 + index * 4 + (kind == "DT"))
                algos[identifier] = raw
            requests[identifier] = state.requests[original]
        entries[str(clone.signal.signal_id)] = clone
    fake.orders.update(orders)
    fake.algos.update(algos)

    def seed(ledger):
        ledger.orders.update(entries)
        ledger.requests.update(requests)

    store.change(c.lease, "TEST_31_HISTORICAL_REQUESTS", seed)
    assert len(store.read("DEMO-test").requests) == 31
    return store, c, fake


def apply(case, token, op=None):
    store, c, _ = case
    return acknowledge_flat_emergency(
        store, c.lease, c.client, op or uuid4(), token, acknowledge_unresolved=True
    )


def test_31_request_preview_checks_every_id_without_database_change(history_case):
    store, c, fake = history_case
    before = store.read("DEMO-test")
    start = len(fake.calls)
    report = review(store, c.client, before)
    assert report["audited_request_count"] == 31 and report["audit_batch_count"] == 4
    assert report["unresolved_count"] == 2 and report["risk_gates"] == "PASS"
    assert store.read("DEMO-test") == before
    queried = set()
    for method, _path, params in fake.calls[start:]:
        assert method == "GET"
        for field in ("origClientOrderId", "clientAlgoId"):
            if field in params:
                queried.add(params[field])
    assert set(before.requests) <= queried
    assert report["signed_query_count"] >= 31 and report["signed_query_count"] <= 600


def test_apply_keeps_all_history_basis_and_pause_and_is_idempotent(history_case):
    store, c, fake = history_case
    before = store.read("DEMO-test")
    token = review(store, c.client, before)["review_token"]
    op = uuid4()
    assert apply(history_case, token, op) == "EMERGENCY_ACKNOWLEDGED_ENTRIES_STILL_PAUSED"
    after = store.read("DEMO-test")
    excluded = {"risk_state", "reconciled", "stream_ready", "operations", "recovery_receipts"}
    assert after.model_dump(exclude=excluded) == before.model_dump(exclude=excluded)
    assert after.risk_state.value == "NORMAL" and after.paused
    assert (
        len(after.requests) == 31
        and after.requests == before.requests
        and after.orders == before.orders
    )
    assert apply(history_case, token, op) == "ALREADY_RECORDED_NO_ACTION"


def last_batch_fault(case, problem):
    store, c, fake = case
    target = sorted(store.read("DEMO-test").requests)[-1]
    source = c.client.send
    hits = 0

    def send(method, path, query, key):
        nonlocal hits
        params = {k: v[0] for k, v in parse_qs(query.get_secret_value()).items()}
        if params.get("origClientOrderId") == target or params.get("clientAlgoId") == target:
            hits += 1
            if hits == 3:  # Two stable snapshots precede the independent request audit.
                if problem == "query":
                    raise DemoUnknown("PRIVATE_SECRET")
                if problem == "missing":
                    return fake.response({"code": -2013}, 400)
                response = source(method, path, query, key)
                raw = json.loads(response.body)
                if problem == "active":
                    raw["status"] = "NEW"
                else:
                    raw["clientOrderId"] = "DE-other"
                return fake.response(raw)
        return source(method, path, query, key)

    c.client.send = send


@pytest.mark.parametrize("problem", ["active", "identity", "query"])
def test_last_batch_problem_cannot_be_hidden_by_first_thirty(history_case, problem):
    store, c, _ = history_case
    before = store.read("DEMO-test")
    last_batch_fault(history_case, problem)
    with pytest.raises((RecoveryBlocked, DemoUnknown)) as result:
        review(store, c.client, before)
    if problem == "query":
        assert isinstance(result.value, DemoUnknown)
    else:
        assert isinstance(result.value, RecoveryBlocked)
        assert result.value.code == (
            "REQUEST_ACTIVE_OR_INVALID" if problem == "active" else "REQUEST_IDENTITY_MISMATCH"
        )
    assert store.read("DEMO-test") == before


def test_last_batch_missing_remains_unresolved_not_rejected_or_removed(history_case):
    store, c, _ = history_case
    before = store.read("DEMO-test")
    last_batch_fault(history_case, "missing")
    report = review(store, c.client, before)
    assert report["audited_request_count"] == 31 and report["unresolved_count"] == 3
    assert store.read("DEMO-test") == before


def test_query_budget_failure_rolls_back_apply_without_ack(history_case, monkeypatch):
    import services.execution.demo_recovery_budget as limits

    store, c, _ = history_case
    before = store.read("DEMO-test")
    token = review(store, c.client, before)["review_token"]
    monkeypatch.setattr(limits, "MAX_SIGNED_QUERIES", 2)
    with pytest.raises(RecoveryBlocked) as result:
        apply(history_case, token)
    assert result.value.code == "REQUEST_AUDIT_QUERY_BUDGET"
    assert store.read("DEMO-test") == before


def test_oversized_history_is_blocked_before_network(history_case):
    store, c, fake = history_case
    state = store.read("DEMO-test")
    for index in range(98):
        state.requests[f"DE-extra-{index}"] = "UNKNOWN"
    start = len(fake.calls)
    with pytest.raises(RecoveryBlocked) as result:
        review(store, c.client, state)
    assert result.value.code == "REQUEST_AUDIT_LIMIT" and len(fake.calls) == start


def test_clock_refresh_during_audit_keeps_original_sixty_second_rule(history_case):
    store, c, fake = history_case
    source = c.client.send
    jumped = False

    def send(method, path, query, key):
        nonlocal jumped
        result = source(method, path, query, key)
        if path == "/fapi/v3/account" and not jumped:
            jumped = True
            fake.at += timedelta(seconds=21)
        return result

    c.client.send = send
    report = review(store, c.client, store.read("DEMO-test"))
    assert report["audit_clock_probes"] >= 1 and report["audited_request_count"] == 31
    assert c.client.clock_ms() - c.client.synced_at < 60000


def test_expired_fence_after_complete_audit_cannot_commit(history_case):
    store, c, fake = history_case
    token = review(store, c.client, store.read("DEMO-test"))["review_token"]
    before = store.read("DEMO-test")
    source = c.client.send
    jumped = False

    def send(method, path, query, key):
        nonlocal jumped
        response = source(method, path, query, key)
        if path == "/fapi/v3/account" and not jumped:
            jumped = True
            fake.at += timedelta(seconds=61)
        return response

    c.client.send = send
    with pytest.raises(RiskRejected):
        apply(history_case, token)
    assert store.read("DEMO-test") == before


def test_final_period_boundary_check_blocks_after_long_audit(history_case):
    store, c, fake = history_case
    store.release(c.lease)
    from datetime import UTC, datetime

    fake.at = datetime(2026, 10, 30, 14, 59, 59, tzinfo=UTC)
    c.lease = store.acquire("DEMO-test", uuid4())
    c.client.sync_clock()
    source = c.client.send
    target = sorted(store.read("DEMO-test").requests)[-1]
    hits = 0

    def send(method, path, query, key):
        nonlocal hits
        params = {k: v[0] for k, v in parse_qs(query.get_secret_value()).items()}
        result = source(method, path, query, key)
        if params.get("origClientOrderId") == target:
            hits += 1
            if hits == 3:
                fake.at += timedelta(seconds=2)
        return result

    c.client.send = send
    before = store.read("DEMO-test")
    with pytest.raises(RecoveryBlocked) as result:
        review(store, c.client, before)
    assert result.value.code == "PERIOD_BASELINE_MISSING"
    assert store.read("DEMO-test") == before


def test_audit_batch_metadata_does_not_change_review_token(history_case, monkeypatch):
    import services.execution.demo_recovery as module

    store, c, _ = history_case
    first = review(store, c.client, store.read("DEMO-test"))
    monkeypatch.setattr(module, "AUDIT_BATCH_SIZE", 7)
    second = review(store, c.client, store.read("DEMO-test"))
    assert first["review_token"] == second["review_token"]
    assert first["audit_batch_count"] == 4 and second["audit_batch_count"] == 5


def test_wall_clock_budget_failure_during_apply_rolls_back_every_change(history_case, monkeypatch):
    import services.execution.demo_recovery_budget as limits

    store, c, _ = history_case
    before = store.read("DEMO-test")
    token = review(store, c.client, before)["review_token"]
    timeline = [0.0]
    original_budget = limits.RecoveryBudget
    monkeypatch.setattr(limits, "RecoveryBudget", lambda: original_budget(lambda: timeline[0]))
    original = c.client.request
    calls = 0

    def request(*args, **kwargs):
        nonlocal calls
        result = original(*args, **kwargs)
        calls += 1
        if calls == 3:
            timeline[0] = 41.0
        return result

    monkeypatch.setattr(c.client, "request", request)
    with pytest.raises(RecoveryBlocked) as result:
        apply(history_case, token)
    assert result.value.code == "REQUEST_AUDIT_TIME_BUDGET"
    assert store.read("DEMO-test") == before


def test_final_transfer_change_is_not_hidden_by_equal_fingerprint(history_case, monkeypatch):
    import services.execution.demo_recovery as module

    store, c, _ = history_case
    before = store.read("DEMO-test")
    original = module.snapshot
    calls = 0

    def snapshot(*args, **kwargs):
        nonlocal calls
        result = original(*args, **kwargs)
        calls += 1
        if calls == 3:
            result.transfers = {"TRANSFER:late": "0"}
        return result

    monkeypatch.setattr(module, "snapshot", snapshot)
    with pytest.raises(RecoveryBlocked) as result:
        review(store, c.client, before)
    assert result.value.code == "SNAPSHOT_CHANGED_DURING_AUDIT"
    assert store.read("DEMO-test") == before
