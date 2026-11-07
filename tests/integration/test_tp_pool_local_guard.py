import importlib.util
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from packages.persistence.demo import accounts, events
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_opposite_tp import exercise_opposite, opposite_case  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_tp_trigger_check import case  # noqa: F401

spec = importlib.util.spec_from_file_location(
    "tp_pool_guard", Path("guard_tp_read_pool_install.py")
)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


@pytest.mark.parametrize(
    "fault", [None, "active_owner", "wrong_operation", "wrong_style", "wrong_marker"]
)
def test_guard_reads_only_exact_closed_unfilled_current_scope(
    opposite_case,  # noqa: F811
    monkeypatch,
    fault,
):
    store, c, fake, options = opposite_case
    options.update(fill=False, trigger=False)
    report = exercise_opposite(opposite_case, monkeypatch)
    operation = UUID(report["operation_id"])
    assert report["filled_quantity"] == "0" or report["filled_quantity"] == "0.0000"
    monkeypatch.setattr(guard, "ACCOUNT", "DEMO-test")
    monkeypatch.setattr(guard, "OPERATION", uuid4() if fault == "wrong_operation" else operation)
    if fault in {"wrong_style", "wrong_marker"}:

        def change(state):
            if fault == "wrong_style":
                state.validation_entry_styles[str(operation)] = "PASSIVE"
            else:
                from uuid import uuid5

                state.operations.pop(str(uuid5(operation, "tp-trigger-observation")))

        store.change(c.lease, "TEST_BAD_INSTALL_SCOPE", change)
    if fault != "active_owner":
        store.release(c.lease)

    def rows():
        with store.engine.connect() as conn:
            return conn.execute(select(accounts)).mappings().all(), conn.execute(
                select(events)
            ).mappings().all()

    before, calls = rows(), list(fake.calls)
    if fault is None:
        local = guard.local(store.engine)
        assert local[0] is None and local[-1].paused
    else:
        with pytest.raises(ValueError):
            guard.local(store.engine)
    assert rows() == before and fake.calls == calls
