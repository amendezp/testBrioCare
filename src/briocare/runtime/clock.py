"""Clock abstraction.

The session machine never spawns threads or timers; it stores deadlines and
compares them against ``clock.now()``.  ``Clock`` is the seam the future voice
layer reuses verbatim — only *who* advances the clock changes.
"""

from __future__ import annotations

import time
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """Monotonic seconds since session start; ``0.0`` at ``StartSession``."""

    def now(self) -> float: ...


class LogicalClock:
    """Manually-advanced clock used by the simulation harness and tests."""

    def __init__(self, start: float = 0.0) -> None:
        self._t = start

    def now(self) -> float:
        return self._t

    def advance(self, dt: float) -> None:
        if dt < 0:
            raise ValueError("cannot advance clock backwards")
        self._t += dt

    def set(self, t: float) -> None:
        if t < self._t:
            raise ValueError("cannot set clock backwards")
        self._t = t


class WallClock:
    """Real monotonic clock; reserved for the future voice milestone."""

    def __init__(self) -> None:
        self._start = time.monotonic()

    def now(self) -> float:
        return time.monotonic() - self._start
