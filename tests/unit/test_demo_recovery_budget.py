"""Recovery GET/clock/time budgets never authorize account mutations."""

import pytest

from integrations.binance_usdm.demo import DemoFailure
from services.execution import demo_recovery_budget as limits
from tests.unit.test_demo_transport import client


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH"])
def test_audit_proxy_blocks_mutation_before_source_transport(method):
    source, fake = client(enabled=False)
    wrapped = limits.RecoveryReadClient(source)
    before = len(fake.calls)
    with pytest.raises(DemoFailure, match="forbids"):
        wrapped.request(method, "/fapi/v1/order", {})
    assert len(fake.calls) == before and wrapped.budget.signed_queries == 0


def test_budget_clock_is_monotonic_and_finite():
    at = [0.0]
    budget = limits.RecoveryBudget(lambda: at[0])
    budget.check()
    at[0] = 40
    with pytest.raises(limits.AuditBudgetExceeded) as result:
        budget.check()
    assert result.value.code == "REQUEST_AUDIT_TIME_BUDGET"
    at[0] = -1
    with pytest.raises(limits.AuditBudgetExceeded):
        budget.check()


def test_signed_limit_blocks_before_next_transport_call():
    source, fake = client(enabled=False)
    wrapped = limits.RecoveryReadClient(source)
    wrapped.budget.signed_queries = limits.MAX_SIGNED_QUERIES
    before = len(fake.calls)
    with pytest.raises(limits.AuditBudgetExceeded) as result:
        wrapped.request("GET", "/fapi/v3/account")
    assert result.value.code == "REQUEST_AUDIT_QUERY_BUDGET" and len(fake.calls) == before


def test_clock_limit_blocks_before_next_probe():
    source, fake = client(enabled=False)
    wrapped = limits.RecoveryReadClient(source)
    wrapped.budget.clock_probes = limits.MAX_CLOCK_PROBES
    before = len(fake.calls)
    with pytest.raises(limits.AuditBudgetExceeded) as result:
        wrapped.sync_clock()
    assert result.value.code == "REQUEST_AUDIT_CLOCK_BUDGET" and len(fake.calls) == before


def test_missing_clock_is_refreshed_without_retrying_signed_request():
    source, fake = client(enabled=False)
    source.synced_at = None
    wrapped = limits.RecoveryReadClient(source)
    before = len(fake.calls)
    wrapped.request("GET", "/fapi/v3/account")
    paths = [p for _, p, _ in fake.calls[before:]]
    assert paths == ["/fapi/v1/time", "/fapi/v3/account"]
    assert wrapped.budget.clock_probes == 1 and wrapped.budget.signed_queries == 1


def test_budget_is_checked_after_query_even_if_result_would_be_not_found(monkeypatch):
    source, _ = client(enabled=False)
    at = [0.0]
    wrapped = limits.RecoveryReadClient(source, limits.RecoveryBudget(lambda: at[0]))

    def late(*args, **kwargs):
        from integrations.binance_usdm.demo import DemoNotFound

        at[0] = 41
        raise DemoNotFound("PRIVATE_SECRET")

    monkeypatch.setattr(source, "request", late)
    with pytest.raises(limits.AuditBudgetExceeded) as result:
        wrapped.request("GET", "/fapi/v1/order")
    assert result.value.code == "REQUEST_AUDIT_TIME_BUDGET"
    assert "PRIVATE_SECRET" not in str(result.value)


def test_write_enabled_source_cannot_be_wrapped():
    source, _ = client(enabled=True)
    with pytest.raises(DemoFailure, match="read-only"):
        limits.RecoveryReadClient(source)
