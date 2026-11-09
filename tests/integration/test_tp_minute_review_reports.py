"""Post-run contracts on natural-time evidence and the offline phase reader."""

import json
import os
from pathlib import Path

import pytest

from scripts.review_tp_minute_budget import review

pytestmark = pytest.mark.skipif(
    not os.getenv("TP_MINUTE_LIFECYCLE_REPORT_DIR"), reason="Completed lifecycle evidence required"
)


@pytest.mark.parametrize(
    "mode",
    ["quiet_tp", "quiet_delayed_cancel", "quiet_failed_cancel", "pulse_50ms", "pulse_50ms_cap"],
)
def test_completed_lifecycle_evidence_and_phase_review_stay_unapproved(mode):
    path = Path(os.environ["TP_MINUTE_LIFECYCLE_REPORT_DIR"]) / (mode + ".json")
    data = json.loads(path.read_text())
    result = review(data)
    assert result["episode_weight"] == sum(data["budget"]["weights"].values())
    assert result["counter_evidence_consistent"]
    assert not result["actual_execution_approved"] and not result["runtime_admission_changed"]
    assert not data["budget"]["actual_tp_execution_approved"]
    if mode.startswith("quiet_"):
        assert data["natural_300_second_test"] and not data["clock_accelerated"]
        assert (
            data["test_observation_seconds"]
            == data["production_observation_seconds_before_run"]
            == 300
        )
        assert data["actual_monotonic_elapsed_seconds"] >= 300
        assert data["result"]["private_session_diagnostic"]["code"] == "NONE"
    if mode == "quiet_tp":
        assert result["conclusion"] == "MEASURED_REQUESTS_FIT_ASSUMPTIONS_ONLY"
        assert result["guarded_peak"]["weight"] <= 5700
        assert (
            data["result"]["tp_trigger_check"]
            == "TP_FILLED_THEN_REMAINING_SL_CANCELED_FLAT_VERIFIED"
        )
    else:
        assert result["conclusion"] == "MEASURED_START_PHASE_CAN_EXCEED_LIMIT"
        assert result["phase_witness_peak"] > 6000
        assert data["result"]["tp_trigger_check"] == "NOT_ACCEPTED"
    if mode == "quiet_delayed_cancel":
        assert result["observed_fixed_minute_peak_own_weight"] < 6000
        assert data["result"]["flat_confirmation"] == "VERIFIED_FLAT"
    if mode == "quiet_failed_cancel":
        assert [x["result"] for x in data["confirmation_attempts"]] == ["UNKNOWN"] * 4
        assert data["result"]["flat_confirmation"] == "UNKNOWN"
    if mode == "pulse_50ms_cap":
        assert data["capture_hint_count"] == data["capture_hint_limit"] == 128
        assert data["confirmation_attempts"][-1]["result"] == "VERIFIED_FLAT"
        assert data["result"]["flat_confirmation"] == "UNKNOWN"
        assert data["result"]["private_session_diagnostic"]["code"] == "DEMO_QUERY_OR_SESSION"
