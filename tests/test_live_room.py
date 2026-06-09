"""The group SessionRoom: a lobby of kids, round-robin cues from kids only.

Runs the room headless with a *fake* monotonic clock and a patched ``asyncio.sleep``
that advances that clock instead of waiting, so the real-time deadline timer fires
deterministically. ``DAILY_API_KEY`` / ``ANTHROPIC_API_KEY`` are unset so video/notes
degrade predictably and nothing hits the network.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
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

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "server" / "scripts" / "group_checkin.yaml"

_START = '{"type":"start"}'
_ADVANCE = '{"type":"override","command":"advance_phase"}'


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
    return SessionRoom(code, load_script(_SCRIPT_PATH), phraser=Phraser(), notes=NoteTaker(), daily=Daily())


async def _add_kid(room: SessionRoom, name: str) -> _FakeWS:
    ws = _FakeWS()
    await room.attach(protocol.KID, ws)
    await room.handle_client_message(protocol.KID, ws, json.dumps({"type": "join", "name": name}))
    return ws


def _spoke(text: str) -> str:
    return json.dumps({"type": "spoke", "text": text})


def _cancel(room: SessionRoom) -> None:
    if room.deadline_task is not None:
        room.deadline_task.cancel()


async def _drain(room: SessionRoom) -> None:
    guard = 0
    while room.deadline_task is not None:
        task = room.deadline_task
        with contextlib.suppress(asyncio.CancelledError):
            await task
        guard += 1
        if guard > 10_000:  # pragma: no cover
            raise RuntimeError("deadline drain did not terminate")
        if room.deadline_task is task:
            break


def _action_kinds(ws: _FakeWS) -> list[str]:
    return [a["kind"] for m in ws.sent if m["type"] == "actions" for a in m["actions"]]


def _invites(ws: _FakeWS) -> list[tuple[str, str]]:
    return [
        (a["participant_id"], a["reason"])
        for m in ws.sent
        if m["type"] == "actions"
        for a in m["actions"]
        if a["kind"] == "invite_participant"
    ]


def _last_snapshot(ws: _FakeWS) -> dict | None:
    snaps = [m["state"] for m in ws.sent if m["type"] == "snapshot"]
    return snaps[-1] if snaps else None


def test_lobby_lists_joined_kids(monkeypatch) -> None:
    _install_fakes(monkeypatch)

    async def scenario() -> _FakeWS:
        room = _make_room("g1")
        ther = _FakeWS()
        await room.attach(protocol.THERAPIST, ther)
        await _add_kid(room, "Maya")
        await _add_kid(room, "Leo")
        return ther

    ther = asyncio.run(scenario())
    lobby = _last_snapshot(ther)["lobby"]
    assert [k["name"] for k in lobby] == ["Maya", "Leo"]
    assert [k["pid"] for k in lobby] == ["kid1", "kid2"]


def test_roster_keeps_join_order_and_round_robins_kids(monkeypatch) -> None:
    _install_fakes(monkeypatch)

    async def scenario() -> _FakeWS:
        room = _make_room("g2")
        ther = _FakeWS()
        await room.attach(protocol.THERAPIST, ther)
        maya = await _add_kid(room, "Maya")  # kid1
        await _add_kid(room, "Leo")  # kid2
        await room.handle_client_message(protocol.THERAPIST, ther, _START)
        _cancel(room)
        await room.handle_client_message(protocol.THERAPIST, ther, _ADVANCE)  # warmup -> go_around
        _cancel(room)
        await room.handle_client_message(protocol.KID, maya, _spoke("happy"))  # kid1 speaks
        _cancel(room)
        return ther

    ther = asyncio.run(scenario())
    rr = [pid for pid, reason in _invites(ther) if reason == "round_robin_turn"]
    # go_around invites kid1 first, then kid2 once kid1 has spoken
    assert rr[:2] == ["kid1", "kid2"]


def test_silent_kid_gets_quiet_nudge(monkeypatch) -> None:
    _install_fakes(monkeypatch)

    async def scenario() -> _FakeWS:
        room = _make_room("g3")
        ther = _FakeWS()
        await room.attach(protocol.THERAPIST, ther)
        await _add_kid(room, "Maya")
        await _add_kid(room, "Leo")
        await room.handle_client_message(protocol.THERAPIST, ther, _START)
        await room.handle_client_message(protocol.THERAPIST, ther, _ADVANCE)  # -> go_around (require_all_speak)
        await _drain(room)  # nobody speaks -> timers fire quiet nudges then advance
        return ther

    ther = asyncio.run(scenario())
    assert any(reason == "quiet_nudge" for _pid, reason in _invites(ther))


def test_per_kid_tagging_and_therapist_only(monkeypatch) -> None:
    _install_fakes(monkeypatch)

    async def scenario() -> _FakeWS:
        room = _make_room("g4")
        ther = _FakeWS()
        await room.attach(protocol.THERAPIST, ther)
        maya = await _add_kid(room, "Maya")
        leo = await _add_kid(room, "Leo")
        await room.handle_client_message(protocol.THERAPIST, ther, _START)
        _cancel(room)
        before = len(_action_kinds(ther))
        await room.handle_client_message(protocol.THERAPIST, ther, _spoke("how is everyone?"))
        assert len(_action_kinds(ther)) == before  # therapist speech never steps the engine
        await room.handle_client_message(protocol.KID, maya, _spoke("happy"))
        _cancel(room)
        await room.handle_client_message(protocol.KID, leo, _spoke("tired"))
        _cancel(room)
        return ther

    ther = asyncio.run(scenario())
    tx = [(m["role"], m["name"], m["text"]) for m in ther.sent if m["type"] == "transcript"]
    assert ("therapist", "Therapist", "how is everyone?") in tx
    assert ("kid", "Maya", "happy") in tx
    assert ("kid", "Leo", "tired") in tx


def test_capacity_caps_at_six_kids(monkeypatch) -> None:
    _install_fakes(monkeypatch)

    async def scenario() -> bool:
        room = _make_room("g5")
        for _i in range(room_mod.MAX_KIDS):
            ok = await room.attach(protocol.KID, _FakeWS())
            assert ok
        return await room.attach(protocol.KID, _FakeWS())  # 7th rejected

    assert asyncio.run(scenario()) is False


def test_room_info_disabled_without_daily_key(monkeypatch) -> None:
    _install_fakes(monkeypatch)

    async def scenario() -> _FakeWS:
        room = _make_room("g6")
        ther = _FakeWS()
        await room.attach(protocol.THERAPIST, ther)
        return ther

    ther = asyncio.run(scenario())
    infos = [m for m in ther.sent if m["type"] == "room_info"]
    assert infos and infos[0]["url"] is None


def test_silent_run_matches_harness(monkeypatch) -> None:
    _install_fakes(monkeypatch)

    async def scenario() -> _FakeWS:
        room = _make_room("g7")
        ther = _FakeWS()
        await room.attach(protocol.THERAPIST, ther)
        await _add_kid(room, "Maya")
        await _add_kid(room, "Leo")
        await room.handle_client_message(protocol.THERAPIST, ther, _START)
        await _drain(room)
        return ther

    ther = asyncio.run(scenario())

    class _Collect:
        def __init__(self) -> None:
            self.kinds: list[str] = []

        def emit(self, actions) -> None:
            self.kinds.extend(a.kind for a in actions)

        def close(self) -> None:
            pass

    sink = _Collect()
    SimulationHarness(load_script(_SCRIPT_PATH), sink).run_events(
        [StartSession(at=0.0, roster={"kid1": "Maya", "kid2": "Leo"})]
    )
    assert _action_kinds(ther) == sink.kinds


def test_kid_cannot_start(monkeypatch) -> None:
    _install_fakes(monkeypatch)

    async def scenario() -> SessionRoom:
        room = _make_room("g8")
        kid = await _add_kid(room, "Maya")
        await room.handle_client_message(protocol.KID, kid, _START)
        return room

    assert asyncio.run(scenario()).machine is None
