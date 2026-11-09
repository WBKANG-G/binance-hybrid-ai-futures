"""Prior pending-probe safety assertions also apply to the new coordinator."""

# ruff: noqa: F811
import pytest

from tests.fixtures.tp_first_confirmation import first_coordinator
from tests.integration import test_tp_staged_confirmation_safety as checks
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_opposite_tp import opposite_case  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_tp_trigger_check import case  # noqa: F401
from tests.integration.test_tp_staged_confirmation_safety import pending  # noqa: F401


@pytest.fixture(autouse=True)
def explicit_first_component(monkeypatch):
    monkeypatch.setattr(checks, "staged_coordinator", first_coordinator)


for name, check in vars(checks).items():
    if name.startswith("test_"):
        globals()["test_first_" + name[5:]] = check
