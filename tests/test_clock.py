from __future__ import annotations

import pytest

from briocare.runtime.clock import Clock, LogicalClock, WallClock


def test_logical_clock_starts_at_zero_and_advances() -> None:
    c = LogicalClock()
    assert c.now() == 0.0
    c.advance(5)
    c.advance(2.5)
    assert c.now() == 7.5
    c.set(10)
    assert c.now() == 10.0


def test_logical_clock_rejects_backwards_motion() -> None:
    c = LogicalClock(start=5.0)
    with pytest.raises(ValueError):
        c.advance(-1)
    with pytest.raises(ValueError):
        c.set(4.9)
    c.set(5.0)  # equal is fine


def test_satisfies_clock_protocol() -> None:
    assert isinstance(LogicalClock(), Clock)
    assert isinstance(WallClock(), Clock)


def test_wall_clock_is_monotonic_nonnegative() -> None:
    w = WallClock()
    a = w.now()
    b = w.now()
    assert a >= 0.0
    assert b >= a
