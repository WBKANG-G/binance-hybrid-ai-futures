"""Reuse the complete entry/cleanup fault assertions with the handoff component."""

# ruff: noqa: F811
from types import SimpleNamespace

import pytest

from tests.fixtures.tp_fenced_handoff import handoff_coordinator
from tests.integration import test_tp_admission_candidate as checks
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_opposite_tp import opposite_case  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_tp_trigger_check import case  # noqa: F401


@pytest.fixture(autouse=True)
def explicit_handoff_component(monkeypatch):
    def create(store, lease, client, budget):
        return handoff_coordinator(store, SimpleNamespace(lease=lease, client=client), budget)

    monkeypatch.setattr(checks, "AdmissionTPTriggerCoordinator", create)


for name, check in vars(checks).items():
    if name.startswith("test_"):
        globals()["test_handoff_" + name[5:]] = check
