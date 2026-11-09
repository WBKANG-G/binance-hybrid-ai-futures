"""Original first-confirmation fault assertions with uncertain-cancel handling."""

# ruff: noqa: F811
import pytest

from tests.fixtures.tp_uncertain_cancel import uncertain_coordinator
from tests.integration import test_tp_first_confirmation_safety as checks
from tests.integration.test_demo import system as demo_system  # noqa: F401
from tests.integration.test_demo_opposite_tp import opposite_case  # noqa: F401
from tests.integration.test_demo_preview import case as preview_case  # noqa: F401
from tests.integration.test_demo_tp_trigger_check import case  # noqa: F401


@pytest.fixture(autouse=True)
def component(monkeypatch):
    monkeypatch.setattr(checks, "first_coordinator", uncertain_coordinator)


test_uncertain_first_faults = (
    checks.test_first_deferral_cannot_bypass_original_final_proof_or_shared_budget
)
