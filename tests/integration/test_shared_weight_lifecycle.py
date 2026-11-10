"""Original managed lifecycle with the additional shared pre-send window."""

# ruff: noqa: F811
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from packages.persistence.demo_shared_weight import SharedWeightJournal
from services.execution.demo_shared_weight_budget import SharedManagedOrderWindowBudget
from tests.integration import test_managed_quota_lifecycle as original
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_opposite_tp import opposite_case  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_tp_trigger_check import case  # noqa: F401


@pytest.mark.parametrize("mode", ["partial", "sl_journal_failure"])
def test_shared_managed_lifecycle(opposite_case, monkeypatch, capsys, tmp_path, mode):
    folder = tmp_path / "weight-scope"
    folder.mkdir(mode=0o700)
    offset = [0.0]
    shared = SharedWeightJournal.create(
        folder,
        "DEMO-lifecycle",
        uuid4(),
        monotonic=lambda: time.monotonic() + offset[0],
        wall_clock=lambda: datetime.now(UTC) + timedelta(seconds=offset[0]),
    )
    offset[0] = 70.01

    def factory(*args, **kwargs):
        return SharedManagedOrderWindowBudget(*args, shared=shared, **kwargs)

    monkeypatch.setattr(original, "ManagedOrderWindowBudget", factory)
    original.test_managed_lifecycle(opposite_case, monkeypatch, capsys, tmp_path, mode)
    assert shared.view()["pressure"] <= 5700 and shared.view()["pending_attempts"] == 0
