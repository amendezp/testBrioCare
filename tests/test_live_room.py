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

from briocare.runtime.actions import InviteParticipant, InviteReason, NoOp
from briocare.runtime.events import StartSession
from briocare.runtime.state import Lifecycle
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
        await room.handle_client_message(protocol.THERAPIST, ther, _ADVANCE)  # checkin -> share_feelings
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
        await room.handle_client_message(protocol.THERAPIST, ther, _ADVANCE)  # -> share_feelings (require_all_speak)
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


def test_snapshot_header_after_start(monkeypatch) -> None:
    _install_fakes(monkeypatch)

    async def scenario() -> _FakeWS:
        room = _make_room("h1")
        ther = _FakeWS()
        await room.attach(protocol.THERAPIST, ther)
        await _add_kid(room, "Maya")
        await room.handle_client_message(protocol.THERAPIST, ther, _START)
        _cancel(room)
        return ther

    snap = _last_snapshot(asyncio.run(scenario()))
    assert snap["started_at"] is not None
    assert snap["activity_total"] == 3  # check-in + tell-us-more + check-out
    assert snap["activity_index"] == 0  # starts on the feelings check-in


def test_engagement_counts_kid_words(monkeypatch) -> None:
    _install_fakes(monkeypatch)

    async def scenario() -> _FakeWS:
        room = _make_room("h2")
        ther = _FakeWS()
        await room.attach(protocol.THERAPIST, ther)
        maya = await _add_kid(room, "Maya")
        await room.handle_client_message(protocol.THERAPIST, ther, _START)
        _cancel(room)
        await room.handle_client_message(protocol.KID, maya, _spoke("I feel happy today"))
        _cancel(room)
        return ther

    snap = _last_snapshot(asyncio.run(scenario()))
    kid1 = next(p for p in snap["participants"] if p["pid"] == "kid1")
    assert kid1["utterances"] == 1
    assert kid1["words"] == 4
    assert kid1["last_spoke_ago"] is not None


def test_friendly_cues_filter_noise(monkeypatch) -> None:
    _install_fakes(monkeypatch)
    room = _make_room("c1")
    nudge = InviteParticipant(
        at=0.0, participant_id="kid1", text="x", reason=InviteReason.QUIET_NUDGE, attempt=1, max_attempts=2
    )
    actions = [NoOp(at=0.0, reason="paused"), nudge]
    cues = room._friendly_cues(actions, {"kid1": "Maya"})
    assert len(cues) == 1  # NoOp dropped
    assert cues[0]["level"] == "action" and "Maya" in cues[0]["text"]


def test_lobby_readiness_flow(monkeypatch) -> None:
    _install_fakes(monkeypatch)
    _READY = '{"type":"ready"}'

    async def scenario() -> tuple[_FakeWS, SessionRoom]:
        room = _make_room("rdy1")
        ther = _FakeWS()
        await room.attach(protocol.THERAPIST, ther)
        maya = await _add_kid(room, "Maya")
        leo = await _add_kid(room, "Leo")
        await room.handle_client_message(protocol.KID, maya, _READY)
        # one ready: lobby reflects it, no "everyone" notice yet
        snap = _last_snapshot(ther)
        assert [(k["name"], k["ready"]) for k in snap["lobby"]] == [("Maya", True), ("Leo", False)]
        assert not any("Everyone's ready" in m.get("text", "") for m in ther.sent if m["type"] == "notice")
        await room.handle_client_message(protocol.KID, leo, _READY)
        return ther, room

    ther, _room = asyncio.run(scenario())
    snap = _last_snapshot(ther)
    assert all(k["ready"] for k in snap["lobby"])  # both ready in the lobby
    assert any("Everyone's ready" in m.get("text", "") for m in ther.sent if m["type"] == "notice")


def test_ready_ignored_once_session_started(monkeypatch) -> None:
    _install_fakes(monkeypatch)

    async def scenario() -> SessionRoom:
        room = _make_room("rdy2")
        ther = _FakeWS()
        await room.attach(protocol.THERAPIST, ther)
        maya = await _add_kid(room, "Maya")
        await room.handle_client_message(protocol.THERAPIST, ther, _START)
        _cancel(room)
        await room.handle_client_message(protocol.KID, maya, '{"type":"ready"}')  # no-op mid-session
        return room

    room = asyncio.run(scenario())
    assert next(iter(room.kids.values())).ready is False


def test_all_done_notice_and_prompt_mirror(monkeypatch) -> None:
    """In the manual share phase, when the last kid shares the therapist gets an
    'Everyone has shared' notice; and every snapshot mirrors the kids' current prompt."""
    _install_fakes(monkeypatch)

    def _rate(v: int) -> str:
        return json.dumps({"type": "rating", "value": v})

    async def scenario() -> _FakeWS:
        room = _make_room("np1")
        ther = _FakeWS()
        await room.attach(protocol.THERAPIST, ther)
        maya = await _add_kid(room, "Maya")
        leo = await _add_kid(room, "Leo")
        await room.handle_client_message(protocol.THERAPIST, ther, _START)  # -> feelings_checkin
        _cancel(room)
        snap = _last_snapshot(ther)
        assert snap["current_prompt"].startswith("🌡️")  # thermometer mirrored to therapist
        await room.handle_client_message(protocol.KID, maya, _rate(4))
        await room.handle_client_message(protocol.KID, leo, _rate(2))  # -> share_feelings (manual)
        _cancel(room)
        assert "go around the circle" in _last_snapshot(ther)["current_prompt"].lower()
        await room.handle_client_message(protocol.KID, maya, _spoke("happy because sunshine"))
        _cancel(room)
        assert not _notices(ther, "Everyone has shared")  # only one kid has shared
        await room.handle_client_message(protocol.KID, leo, _spoke("nervous about school"))
        _cancel(room)
        # manual phase: still in share_feelings, and the therapist got the all-done cue
        assert room.machine.state.phase is not None
        assert room.machine.state.phase.phase_id == "share_feelings"
        return ther

    def _notices(ws: _FakeWS, text: str) -> list[dict]:
        return [m for m in ws.sent if m["type"] == "notice" and text in m.get("text", "")]

    ther = asyncio.run(scenario())
    assert _notices(ther, "Everyone has shared")


def test_kid_cannot_start(monkeypatch) -> None:
    _install_fakes(monkeypatch)

    async def scenario() -> SessionRoom:
        room = _make_room("g8")
        kid = await _add_kid(room, "Maya")
        await room.handle_client_message(protocol.KID, kid, _START)
        return room

    assert asyncio.run(scenario()).machine is None


def test_quick_reply_relays_to_group_and_takes_the_turn(monkeypatch) -> None:
    _install_fakes(monkeypatch)

    async def scenario() -> tuple[_FakeWS, _FakeWS]:
        room = _make_room("qr1")
        ther = _FakeWS()
        await room.attach(protocol.THERAPIST, ther)
        maya = await _add_kid(room, "Maya")  # kid1
        await _add_kid(room, "Leo")  # kid2
        await room.handle_client_message(protocol.THERAPIST, ther, _START)
        _cancel(room)
        await room.handle_client_message(protocol.THERAPIST, ther, _ADVANCE)  # checkin -> share_feelings (kid1's turn)
        _cancel(room)
        await room.handle_client_message(protocol.KID, maya, json.dumps({"type": "quick_reply", "text": "😟 nervous"}))
        _cancel(room)
        return ther, maya

    ther, maya = asyncio.run(scenario())
    bubbles = [m["text"] for m in maya.sent if m["type"] == "assistant"]
    assert any(t == "Maya wants us to know: 😟 nervous" for t in bubbles)  # auto-relayed to the circle
    # the tap counted as kid1's turn, so the round-robin moved on to kid2
    assert ("kid2", "round_robin_turn") in _invites(ther)


def test_private_nudge_reaches_only_the_target_kid(monkeypatch) -> None:
    _install_fakes(monkeypatch)

    async def scenario() -> tuple[_FakeWS, _FakeWS]:
        room = _make_room("pn1")
        ther = _FakeWS()
        await room.attach(protocol.THERAPIST, ther)
        maya = await _add_kid(room, "Maya")  # kid1
        leo = await _add_kid(room, "Leo")  # kid2
        await room.handle_client_message(protocol.THERAPIST, ther, _START)
        _cancel(room)
        await room.handle_client_message(protocol.THERAPIST, ther, json.dumps({"type": "private_nudge", "pid": "kid2"}))
        return maya, leo

    maya, leo = asyncio.run(scenario())
    assert any(m["type"] == "private_prompt" for m in leo.sent)  # the target gets it
    assert not any(m["type"] == "private_prompt" for m in maya.sent)  # nobody else does


def test_rating_populates_snapshot_checkin(monkeypatch) -> None:
    _install_fakes(monkeypatch)

    async def scenario() -> _FakeWS:
        room = _make_room("rt1")
        ther = _FakeWS()
        await room.attach(protocol.THERAPIST, ther)
        await _add_kid(room, "Maya")  # kid1
        leo = await _add_kid(room, "Leo")  # kid2
        await room.handle_client_message(protocol.THERAPIST, ther, _START)  # enters feelings_checkin (rating)
        maya_ws = next(c.ws for c in room.kids.values() if c.pid == "kid1")
        await room.handle_client_message(protocol.KID, maya_ws, json.dumps({"type": "rating", "value": 4}))
        await room.handle_client_message(protocol.KID, leo, json.dumps({"type": "rating", "value": 2}))
        _cancel(room)
        return ther

    snap = _last_snapshot(asyncio.run(scenario()))
    kid1 = next(p for p in snap["participants"] if p["pid"] == "kid1")
    assert kid1["rating_checkin"] == 4  # ratings survive into later snapshots for the trend


def test_kid_cannot_end_the_group_session(monkeypatch) -> None:
    _install_fakes(monkeypatch)

    async def scenario() -> SessionRoom:
        room = _make_room("ke1")
        ther = _FakeWS()
        await room.attach(protocol.THERAPIST, ther)
        maya = await _add_kid(room, "Maya")
        await room.handle_client_message(protocol.THERAPIST, ther, _START)
        _cancel(room)
        await room.handle_client_message(protocol.KID, maya, '{"type":"end"}')  # must be ignored
        _cancel(room)
        return room

    room = asyncio.run(scenario())
    assert room.machine is not None and room.machine.state.lifecycle != Lifecycle.ENDED


def test_kid_leaving_notifies_therapist(monkeypatch) -> None:
    _install_fakes(monkeypatch)

    async def scenario() -> _FakeWS:
        room = _make_room("kl1")
        ther = _FakeWS()
        await room.attach(protocol.THERAPIST, ther)
        maya = await _add_kid(room, "Maya")
        await room.handle_client_message(protocol.THERAPIST, ther, _START)
        _cancel(room)
        await room.detach(protocol.KID, maya)
        return ther

    ther = asyncio.run(scenario())
    assert any(m["type"] == "notice" and "left the session" in m["text"] for m in ther.sent)


def test_session_review_is_per_kid_and_transcript_isolated(monkeypatch) -> None:
    _install_fakes(monkeypatch)

    async def scenario() -> _FakeWS:
        room = _make_room("rev1")
        ther = _FakeWS()
        await room.attach(protocol.THERAPIST, ther)
        maya = await _add_kid(room, "Maya")  # kid1
        leo = await _add_kid(room, "Leo")  # kid2
        await room.handle_client_message(protocol.THERAPIST, ther, _START)
        _cancel(room)
        await room.handle_client_message(protocol.KID, maya, _spoke("i feel happy"))
        _cancel(room)
        await room.handle_client_message(protocol.KID, leo, _spoke("i worry about Maya"))
        _cancel(room)
        await room.handle_client_message(protocol.THERAPIST, ther, '{"type":"end"}')
        if room._notes_task is not None:
            await room._notes_task
        _cancel(room)
        return ther

    ther = asyncio.run(scenario())
    reviews = [m for m in ther.sent if m["type"] == "session_review"]
    assert reviews, "the therapist should receive a session_review at the end"
    kids = {k["pid"]: k for k in reviews[-1]["kids"]}
    assert set(kids) == {"kid1", "kid2"}
    # each child's review carries only their OWN transcript lines
    assert [t["text"] for t in kids["kid1"]["transcript"]] == ["i feel happy"]
    assert [t["text"] for t in kids["kid2"]["transcript"]] == ["i worry about Maya"]
    assert "Maya" not in " ".join(t["text"] for t in kids["kid1"]["transcript"])


def test_session_end_sends_final_notes_and_writes_dump(tmp_path, monkeypatch) -> None:
    _install_fakes(monkeypatch)
    monkeypatch.setenv("BRIOCARE_DUMP_DIR", str(tmp_path))

    async def scenario() -> _FakeWS:
        room = _make_room("d1")
        ther = _FakeWS()
        await room.attach(protocol.THERAPIST, ther)
        maya = await _add_kid(room, "Maya")
        await room.handle_client_message(protocol.THERAPIST, ther, _START)
        _cancel(room)
        await room.handle_client_message(protocol.KID, maya, _spoke("i feel happy"))
        _cancel(room)
        await room.handle_client_message(protocol.THERAPIST, ther, '{"type":"end"}')
        if room._notes_task is not None:
            await room._notes_task  # let the final note + parent summary + dump finish
        _cancel(room)
        return ther

    ther = asyncio.run(scenario())
    # the existing end-of-session final note is preserved
    assert any(m["type"] == "notes" and m["final"] for m in ther.sent)
    # and a fail-silent JSON dump was written with the transcript
    files = list(tmp_path.glob("*.json"))
    assert files, "a session dump should be written on end"
    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert data["code"] == "d1"
    assert any("happy" in e.get("text", "") for e in data["transcript"])


def test_full_session_review_shows_checkin_checkout_trend(monkeypatch) -> None:
    """Deterministic end-to-end: drive real rating messages through a whole session to
    feelings_checkout and confirm the review lobby shows each kid's check-in -> check-out
    trend (the thing the live E2E couldn't test without racing on auto-advance)."""
    _install_fakes(monkeypatch)

    def _rate(v: int) -> str:
        return json.dumps({"type": "rating", "value": v})

    async def scenario() -> list[dict]:
        room = _make_room("full1")
        ther = _FakeWS()
        await room.attach(protocol.THERAPIST, ther)
        maya = await _add_kid(room, "Maya")
        leo = await _add_kid(room, "Leo")
        await room.handle_client_message(protocol.THERAPIST, ther, _START)  # -> feelings_checkin
        _cancel(room)
        await room.handle_client_message(protocol.KID, maya, _rate(4))
        await room.handle_client_message(protocol.KID, leo, _rate(2))  # both rated -> warmup
        _cancel(room)
        await room.handle_client_message(protocol.THERAPIST, ther, _ADVANCE)  # checkin -> share_feelings
        _cancel(room)
        await room.handle_client_message(protocol.KID, maya, _spoke("happy"))
        _cancel(room)
        await room.handle_client_message(protocol.KID, leo, _spoke("nervous"))  # both spoke -> reflect
        _cancel(room)
        # advance until we're actually in feelings_checkout (don't overshoot)
        for _ in range(4):
            ph = room.machine.state.phase
            if ph is not None and ph.phase_id == "feelings_checkout":
                break
            await room.handle_client_message(protocol.THERAPIST, ther, _ADVANCE)
            _cancel(room)
        assert room.machine.state.phase is not None
        assert room.machine.state.phase.phase_id == "feelings_checkout"
        await room.handle_client_message(protocol.KID, maya, _rate(5))
        await room.handle_client_message(protocol.KID, leo, _rate(3))  # both rated -> auto-end
        _cancel(room)
        # ratings landed under the linear check-out phase, and the review reads them
        assert room.machine.state.ratings.get("feelings_checkout", {}).get("kid1") == 5
        return await room._build_kid_reviews(list(room.transcript))

    kids = {k["pid"]: k for k in asyncio.run(scenario())}
    assert kids["kid1"]["rating_checkin"] == 4 and kids["kid1"]["rating_checkout"] == 5
    assert kids["kid2"]["rating_checkin"] == 2 and kids["kid2"]["rating_checkout"] == 3


def test_review_checkout_ignores_menu_thermometer(monkeypatch) -> None:
    """Found in the live E2E: the review builder picked act_thermometer (menu_only,
    last rating phase in the script) as 'check-out', so the trend came back empty."""
    _install_fakes(monkeypatch)

    async def scenario() -> dict:
        room = _make_room("trend1")
        ther = _FakeWS()
        await room.attach(protocol.THERAPIST, ther)
        await _add_kid(room, "Maya")
        await room.handle_client_message(protocol.THERAPIST, ther, _START)
        _cancel(room)
        m = room.machine
        m.state.ratings = {"feelings_checkin": {"kid1": 4}, "feelings_checkout": {"kid1": 5}}
        kids = await room._build_kid_reviews(list(room.transcript))
        return kids[0]

    kid = asyncio.run(scenario())
    assert kid["rating_checkin"] == 4
    assert kid["rating_checkout"] == 5  # feelings_checkout, NOT the menu-only act_thermometer


def test_snapshot_lists_activities_and_goto_launches_one(monkeypatch) -> None:
    _install_fakes(monkeypatch)

    async def scenario() -> tuple[dict, _FakeWS]:
        room = _make_room("act1")
        ther = _FakeWS()
        await room.attach(protocol.THERAPIST, ther)
        await _add_kid(room, "Maya")
        await room.handle_client_message(protocol.THERAPIST, ther, _START)
        _cancel(room)
        first = _last_snapshot(ther)
        goto = json.dumps({"type": "override", "command": "goto_phase", "args": {"phase_id": "act_compliments"}})
        await room.handle_client_message(protocol.THERAPIST, ther, goto)
        _cancel(room)
        return first, ther

    first, ther = asyncio.run(scenario())
    assert any(a["id"] == "act_compliments" for a in first["activities"])  # library is advertised
    assert _last_snapshot(ther)["phase_id"] == "act_compliments"  # button launches it
