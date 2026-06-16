"""Demo-hardening: room codes are reusable after a session ends, and Daily room
creation retries after a transient failure instead of disabling video forever."""

from __future__ import annotations

import asyncio
from pathlib import Path

import server.app as appmod
from server.notes import NoteTaker
from server.phraser import Phraser
from server.room import SessionRoom

from briocare.runtime.machine import SessionMachine
from briocare.runtime.state import Lifecycle
from briocare.scripts.loader import load_script

_SCRIPT = Path(__file__).resolve().parents[1] / "server" / "scripts" / "group_checkin.yaml"


def test_ended_room_code_is_reusable() -> None:
    appmod._rooms.clear()
    try:
        first = appmod._get_or_create_room("reuse1")
        assert appmod._get_or_create_room("reuse1") is first  # same room while live

        first.machine = SessionMachine(first.script)
        first.machine.state.lifecycle = Lifecycle.ENDED  # simulate a finished session

        second = appmod._get_or_create_room("reuse1")
        assert second is not first  # ended -> a fresh room for the same code
        assert second.machine is None
    finally:
        appmod._rooms.clear()


class _StubDaily:
    def __init__(self, results: list[str | None], *, enabled: bool = True) -> None:
        self._results = list(results)
        self.enabled = enabled
        self.calls = 0

    async def get_or_create_room(self, code: str) -> str | None:
        self.calls += 1
        return self._results.pop(0) if self._results else None


def _room(code: str, daily: _StubDaily) -> SessionRoom:
    return SessionRoom(code, load_script(_SCRIPT), phraser=Phraser(), notes=NoteTaker(), daily=daily)


def test_ensure_room_retries_after_transient_failure() -> None:
    daily = _StubDaily([None, "https://x.daily.co/abc"])
    room = _room("c1", daily)
    assert asyncio.run(room._ensure_room()) is None  # transient failure
    assert room._room_fetched is False  # not latched -> will retry
    assert asyncio.run(room._ensure_room()) == "https://x.daily.co/abc"  # retried, now succeeds
    assert room._room_fetched is True
    assert daily.calls == 2


def test_ensure_room_latches_when_video_disabled() -> None:
    daily = _StubDaily([], enabled=False)
    room = _room("c2", daily)
    assert asyncio.run(room._ensure_room()) is None
    assert room._room_fetched is True  # nothing to retry when video is off
    assert asyncio.run(room._ensure_room()) is None
    assert daily.calls == 1  # second call short-circuits
