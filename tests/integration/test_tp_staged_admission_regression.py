"""Run the existing admission fault matrix against the new staged coordinator."""

# ruff: noqa: F811
from types import SimpleNamespace

import pytest

from tests.fixtures.tp_staged_confirmation import staged_coordinator
from tests.integration import test_tp_admission_candidate as checks
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_opposite_tp import opposite_case  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_tp_trigger_check import case  # noqa: F401


@pytest.fixture(autouse=True)
def explicit_staged_component(monkeypatch):
    def create(store, lease, client, budget):
        return staged_coordinator(store, SimpleNamespace(lease=lease, client=client), budget)

    monkeypatch.setattr(checks, "AdmissionTPTriggerCoordinator", create)


# Preserve the exact original assertions and parametrization for full/partial/
# unfilled entries, pagination, budget stop, response loss and shared-IP limits.
for name, check in vars(checks).items():
    if name.startswith("test_"):
        globals()["test_staged_" + name[5:]] = check
