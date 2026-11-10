"""Bad initial cost policy must fail before journals, SQL inspection or HTTP."""

# ruff: noqa: F811
import pytest

from packages.persistence.demo_order_quota import LocalQuotaStopped
from services.execution import demo_managed_runtime as runtime
from services.execution import demo_query_contract as queries
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_managed_order_quota import managed  # noqa: F401
from tests.integration.test_managed_runtime_assembly import assembly, open_session  # noqa: F401
from tests.integration.test_quota_startup_review import snapshot
from tests.unit.test_quota_startup_read_only import files


@pytest.mark.parametrize(
    "fault", ["read_undercharge", "write_undercharge", "missing", "extra", "query_shape"]
)
def test_initial_policy_mismatch_refused_before_state_access(assembly, monkeypatch, fault):
    costs = dict(runtime.MODELED_TP_ROUTE_COSTS)
    if fault == "read_undercharge":
        costs[("GET", "/fapi/v1/accountConfig")] = 1
    elif fault == "write_undercharge":
        costs[("POST", "/fapi/v1/order")] = 1
    elif fault == "missing":
        del costs[("DELETE", "/fapi/v1/algoOrder")]
    elif fault == "extra":
        costs[("GET", "/fapi/v1/depth")] = 1
    else:
        monkeypatch.setitem(
            queries.PUBLIC,
            "/fapi/v1/klines",
            {"symbol": "BTCUSDT", "interval": "1m", "limit": "1500"},
        )
    monkeypatch.setattr(runtime, "MODELED_TP_ROUTE_COSTS", costs)
    store = assembly[0]
    storage = assembly[8]
    before = snapshot(store), files(storage.order_directory), files(storage.shared_directory)
    accessed = []
    original = runtime._current_ledger

    def read(*args):
        accessed.append(True)
        return original(*args)

    monkeypatch.setattr(runtime, "_current_ledger", read)
    with pytest.raises(LocalQuotaStopped, match="RUNTIME_COST_CONTRACT"):
        session = open_session(assembly, allow_synthetic_model_execution=True)
        session.close()
    assert not accessed and not assembly[7]
    assert before == (
        snapshot(store),
        files(storage.order_directory),
        files(storage.shared_directory),
    )


def test_query_change_after_assembly_refused_before_any_new_charge(assembly, monkeypatch):
    from pydantic import SecretStr

    session = open_session(assembly, allow_synthetic_model_execution=True)
    before = (
        snapshot(assembly[0]),
        files(assembly[8].order_directory),
        files(assembly[8].shared_directory),
    )
    monkeypatch.setitem(
        queries.PUBLIC, "/fapi/v1/klines", {"symbol": "BTCUSDT", "interval": "1m", "limit": "1500"}
    )
    try:
        with pytest.raises(LocalQuotaStopped, match="RUNTIME_COST_CONTRACT"):
            session.budget("GET", "/fapi/v1/time", SecretStr(""), None)
        assert before == (
            snapshot(assembly[0]),
            files(assembly[8].order_directory),
            files(assembly[8].shared_directory),
        )
        assert not assembly[7]
    finally:
        session.close()


def test_default_execution_refusal_still_precedes_policy_access(monkeypatch):
    def forbidden(*args):
        raise AssertionError("policy accessed before default refusal")

    monkeypatch.setattr(runtime, "require_runtime_cost_contract", forbidden)
    with pytest.raises(LocalQuotaStopped, match="REAL_EXECUTION_DISABLED"):
        runtime.assemble_managed_runtime(None, None, None, None, None)
