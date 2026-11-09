"""Original admission fault matrix with the new component."""

# ruff: noqa: F811
from types import SimpleNamespace

import pytest

from tests.fixtures.tp_uncertain_cancel import uncertain_coordinator
from tests.integration import test_tp_admission_candidate as checks
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_opposite_tp import opposite_case  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_tp_trigger_check import case  # noqa: F401


@pytest.fixture(autouse=True)
def component(monkeypatch):
    monkeypatch.setattr(
        checks,
        "AdmissionTPTriggerCoordinator",
        lambda store, lease, client, budget: uncertain_coordinator(
            store, SimpleNamespace(lease=lease, client=client), budget
        ),
    )


for name, check in vars(checks).items():
    if name.startswith("test_"):
        globals()["test_uncertain_" + name[5:]] = check
