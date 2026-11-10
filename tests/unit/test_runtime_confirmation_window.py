"""Deadline arithmetic accepts finite monotonic progress and never refreshes its end."""

import pytest

from services.execution.demo_runtime_confirmation import (
    ConfirmationWindow,
    ConfirmationWindowClosed,
)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), True, None, "1"])
def test_invalid_clock_cannot_open_window(value):
    with pytest.raises(ConfirmationWindowClosed, match="CLOCK_UNTRUSTED"):
        ConfirmationWindow(lambda: value)


def test_clock_regression_is_not_remaining_time_credit():
    ticks = iter([100.0, 99.0])
    window = ConfirmationWindow(lambda: next(ticks))
    with pytest.raises(ConfirmationWindowClosed, match="CLOCK_UNTRUSTED"):
        window.remaining()


def test_clock_exception_is_sanitized_and_closed():
    def clock():
        raise ValueError("synthetic-private-detail")

    with pytest.raises(ConfirmationWindowClosed) as error:
        ConfirmationWindow(clock)
    assert str(error.value) == "CLOCK_UNTRUSTED"


@pytest.mark.parametrize("age", [119.999, 120.0, 120.001])
def test_start_deadline_boundary(age):
    ticks = iter([0.0, age])
    window = ConfirmationWindow(lambda: next(ticks))
    if age < 120:
        assert window.remaining() == pytest.approx(0.001)
    else:
        with pytest.raises(ConfirmationWindowClosed, match="START_DEADLINE_REACHED"):
            window.remaining()


def test_remaining_checks_do_not_reset_deadline():
    ticks = iter([100.0, 150.0, 200.0, 220.0])
    window = ConfirmationWindow(lambda: next(ticks))
    assert window.remaining() == 70.0 and window.remaining() == 20.0
    with pytest.raises(ConfirmationWindowClosed, match="START_DEADLINE_REACHED"):
        window.remaining()
