"""The prior handoff's complete fault cases must retain their safety assertions."""

# ruff: noqa: F811
import pytest

from tests.fixtures.tp_first_confirmation import first_coordinator
from tests.integration import test_tp_handoff_safety as checks
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_opposite_tp import opposite_case  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_tp_trigger_check import case  # noqa: F401


@pytest.fixture(autouse=True)
def explicit_first_component(monkeypatch):
    monkeypatch.setattr(checks, "handoff_coordinator", first_coordinator)


test_first_handoff_faults = (
    checks.test_handoff_changes_failure_and_possible_send_never_duplicate_exit
)
