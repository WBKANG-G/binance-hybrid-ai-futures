import importlib.util
from pathlib import Path
from uuid import UUID, uuid4, uuid5

import pytest
from sqlalchemy import select

from packages.persistence.demo import accounts, events
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_opposite_tp import exercise_opposite, opposite_case  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_tp_trigger_check import case  # noqa: F401

spec = importlib.util.spec_from_file_location(
    "local_fix_guard", Path("guard_serial_fix_install.py")
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


@pytest.mark.parametrize(
    "fault", [None, "active_owner", "wrong_operation", "missing_ready", "child_failure"]
)
def test_real_local_guard_reads_only_closed_scoped_flat_state(opposite_case, monkeypatch, fault):  # noqa: F811
    store, c, fake, options = opposite_case
    options["trigger"] = False
    result = exercise_opposite(opposite_case, monkeypatch)
    operation = UUID(result["operation_id"])

    def mark(state):
        if fault != "missing_ready":
            state.operations[str(uuid5(operation, "protected-worker-ready"))] = (
                "FULL_PROTECTED_READY"
            )
        state.operations[str(uuid5(operation, "protected-worker-finally"))] = "FINALLY_RAN"
        state.operations[str(uuid5(operation, "protected-worker-failure"))] = (
            '{"stage":"STREAM_OBSERVATION","code":"DB_LOCK_TIMEOUT"}'
            if fault == "child_failure"
            else '{"stage":"STREAM_OBSERVATION","code":"NONE"}'
        )

    store.change(c.lease, "TEST_PROBE_INSTALL_CONTEXT", mark)
    monkeypatch.setattr(m, "ACCOUNT", "DEMO-test")
    monkeypatch.setattr(m, "OPERATION", uuid4() if fault == "wrong_operation" else operation)
    if fault != "active_owner":
        store.release(c.lease)

    def snapshot():
        with store.engine.connect() as conn:
            return conn.execute(select(accounts)).mappings().all(), conn.execute(
                select(events)
            ).mappings().all()

    before = snapshot()
    calls = list(fake.calls)
    if fault is None:
        state = m.local(store.engine)
        assert state[0] is None and state[-1].paused
    else:
        with pytest.raises(ValueError):
            m.local(store.engine)
    assert snapshot() == before and fake.calls == calls
