"""The reframed SessionRoom: human transcript + engine cues from the kid only.

Runs the room headless with a *fake* monotonic clock and a patched ``asyncio.sleep``
that advances that clock instead of waiting, so the real-time deadline timer fires
deterministically. ``DAILY_API_KEY`` / ``ANTHROPIC_API_KEY`` are unset so video/notes
degrade predictably and nothing hits the network.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

from server import protocol
from server import room as room_mod
from server.daily import Daily
from server.notes import NoteTaker
from server.phraser import Phraser
from server.room import SessionRoom

from briocare.runtime.events import StartSession
from briocare.scripts.loader import load_script
from briocare.sim.harness import SimulationHarness

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "server" / "scripts" / "solo_checkin.yaml"


class _FakeWall:
    instance: _FakeWall | None = None

    def __init__(self) -> None:
        self.t = 0.0
        _FakeWall.instance = self

    def now(self) -> float:
        return self.t


class _FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, obj: dict) -> None:
        self.sent.append(obj)


def _install_fakes(monkeypatch) -> None:
    monkeypatch.delenv("DAILY_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _FakeWall.instance = None
    monkeypatch.setattr(room_mod, "WallClock", _FakeWall)
    real_sleep = asyncio.sleep

    async def fake_sleep(delay: float = 0.0) -> None:
        if delay and _FakeWall.instance is not None:
            _FakeWall.instance.t += delay
        await real_sleep(0)

    monkeypatch.setattr(room_mod.asyncio, "sleep", fake_sleep)


def _make_room(code: str) -> SessionRoom:
    return SessionRoom(
        code,
        load_script(_SCRIPT_PATH),
        phraser=Phraser(),
        notes=NoteTaker(),
        daily=Daily(),
    )


async def _drain_deadlines(room: SessionRoom) -> None:
    guard = 0
    while room.deadline_task is not None:
        task = room.deadline_task
        with contextlib.suppress(asyncio.CancelledError):
            await task
        guard += 1
        if guard > 10_000:  # pragma: no cover - safety net
            raise RuntimeError("room deadline drain did not terminate")
        if room.deadline_task is task:
            break


def _action_kinds(ws: _FakeWS) -> list[str]:
    return [a["kind"] for m in ws.sent if m["type"] == "actions" for a in m["actions"]]


def _harness_action_kinds(script) -> list[str]:
    class _Collect:
        def __init__(self) -> None:
            self.kinds: list[str] = []

        def emit(self, actions) -> None:
            self.kinds.extend(a.kind for a in actions)

        def close(self) -> None:
            pass

    sink = _Collect()
    SimulationHarness(script, sink).run_events([StartSession(at=0.0, roster={"kid1": "Friend"})])
    return sink.kinds


def test_silent_run_matches_harness(monkeypatch) -> None:
    _install_fakes(monkeypatch)

    async def scenario() -> _FakeWS:
        room = _make_room("t1")
        ther, kid = _FakeWS(), _FakeWS()
        await room.attach(protocol.THERAPIST, ther)
        await room.attach(protocol.KID, kid)
        await room.handle_client_message(protocol.THERAPIST, '{"type":"start"}')
        await _drain_deadlines(room)
        return ther

    ther = asyncio.run(scenario())
    assert _action_kinds(ther) == _harness_action_kinds(load_script(_SCRIPT_PATH))


def test_room_info_disabled_without_daily_key(monkeypatch) -> None:
    _install_fakes(monkeypatch)

    async def scenario() -> _FakeWS:
        room = _make_room("t2")
        ther = _FakeWS()
        await room.attach(protocol.THERAPIST, ther)
        return ther

    ther = asyncio.run(scenario())
    room_infos = [m for m in ther.sent if m["type"] == "room_info"]
    assert room_infos and room_infos[0]["url"] is None


def test_kid_speech_drives_engine_and_transcript(monkeypatch) -> None:
    _install_fakes(monkeypatch)

    async def scenario() -> _FakeWS:
        room = _make_room("t3")
        ther = _FakeWS()
        await room.attach(protocol.THERAPIST, ther)
        await room.attach(protocol.KID, _FakeWS())
        await room.handle_client_message(protocol.THERAPIST, '{"type":"start"}')
        if room.deadline_task is not None:
            room.deadline_task.cancel()
        ther.sent.clear()
        await room.handle_client_message(protocol.KID, '{"type":"spoke","text":"I feel happy"}')
        if room.deadline_task is not None:
            room.deadline_task.cancel()
        return ther

    ther = asyncio.run(scenario())
    # The kid's words are transcribed...
    tx = [m for m in ther.sent if m["type"] == "transcript"]
    assert tx and tx[-1]["role"] == "kid" and "happy" in tx[-1]["text"]
    # ...and they drive the engine (acknowledge_speaker on a kid utterance in this script).
    assert "acknowledge_speaker" in _action_kinds(ther)


def test_therapist_speech_is_transcript_only(monkeypatch) -> None:
    _install_fakes(monkeypatch)

    async def scenario() -> _FakeWS:
        room = _make_room("t4")
        ther = _FakeWS()
        await room.attach(protocol.THERAPIST, ther)
        await room.attach(protocol.KID, _FakeWS())
        await room.handle_client_message(protocol.THERAPIST, '{"type":"start"}')
        if room.deadline_task is not None:
            room.deadline_task.cancel()
        before = len(_action_kinds(ther))
        await room.handle_client_message(protocol.THERAPIST, '{"type":"spoke","text":"How was your week?"}')
        after = len(_action_kinds(ther))
        assert after == before  # therapist speech does not step the engine
        return ther

    ther = asyncio.run(scenario())
    tx = [m for m in ther.sent if m["type"] == "transcript"]
    assert tx[-1]["role"] == "therapist" and "week" in tx[-1]["text"]


def test_notes_fall_back_to_transcript(monkeypatch) -> None:
    _install_fakes(monkeypatch)

    async def scenario() -> _FakeWS:
        room = _make_room("t5")
        ther = _FakeWS()
        await room.attach(protocol.THERAPIST, ther)
        # six therapist utterances trigger a notes refresh without touching the engine
        for i in range(6):
            await room.handle_client_message(protocol.THERAPIST, f'{{"type":"spoke","text":"note line {i}"}}')
        if room._notes_task is not None:
            await room._notes_task
        return ther

    ther = asyncio.run(scenario())
    notes = [m for m in ther.sent if m["type"] == "notes"]
    assert notes and "note line 0" in notes[-1]["markdown"]


def test_kid_cannot_start(monkeypatch) -> None:
    _install_fakes(monkeypatch)

    async def scenario() -> SessionRoom:
        room = _make_room("t6")
        await room.attach(protocol.KID, _FakeWS())
        await room.handle_client_message(protocol.KID, '{"type":"start"}')
        return room

    assert asyncio.run(scenario()).machine is None
