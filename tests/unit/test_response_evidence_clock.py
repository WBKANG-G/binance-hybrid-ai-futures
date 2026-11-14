"""Clock policy denies invalid samples without storage access or repair."""

from datetime import UTC, datetime, timedelta

import pytest

from packages.persistence.demo_order_quota import LocalQuotaStopped
from packages.persistence.demo_response_evidence import verify_clock


@pytest.mark.parametrize(
    "sample", ["boot", "backward", "nan", "infinity", "bool", "drift", "naive", "offset"]
)
def test_invalid_clock_requires_review(sample):
    wall = datetime(2026, 11, 14, tzinfo=UTC)
    meta = dict(boot="same-boot", start=100.0, last=170.001, wall=wall.timestamp())
    now, boot = 180.0, "same-boot"
    wall += timedelta(seconds=80)
    if sample == "boot":
        boot = "different-boot"
    if sample == "backward":
        now = 160.0
    if sample == "nan":
        now = float("nan")
    if sample == "infinity":
        now = float("inf")
    if sample == "bool":
        now = True
    if sample == "drift":
        wall += timedelta(seconds=1.001)
    if sample == "naive":
        wall = wall.replace(tzinfo=None)
    if sample == "offset":
        wall = wall.astimezone(__import__("datetime").timezone(timedelta(hours=9)))
    before = dict(meta)
    with pytest.raises(LocalQuotaStopped, match="CLOCK_REVIEW_REQUIRED"):
        verify_clock(meta, now, wall, boot)
    assert meta == before


def test_same_boot_process_restart_and_one_second_drift_are_accepted():
    wall = datetime(2026, 11, 14, tzinfo=UTC)
    meta = dict(boot="same-boot", start=100.0, last=170.001, wall=wall.timestamp())
    verify_clock(meta, 180.0, wall + timedelta(seconds=81), "same-boot")
