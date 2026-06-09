"""A single live group session: one therapist + up to six kids + an AI co-pilot.

Each kid joins on their **own** WebSocket connection with a display name, so the room
always knows who is speaking (no audio diarization). The kids' utterances drive the
existing :class:`SessionMachine` (round-robin turn-taking, quiet-kid nudges, pacing);
the therapist's utterances are transcript/notes only. The therapist sees a **lobby** of
joined kids and clicks Start, which freezes the roster (join order = turn order).

The real-time scaffolding (one ``WallClock`` + a single asyncio deadline timer + a lock
serialising every ``machine.step``) is unchanged.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import Any

from briocare.io.facilitator_sink import render_action
from briocare.runtime.actions import (
    AcknowledgeSpeaker,
    EndSession,
    InviteParticipant,
    PromptSource,
    SayPrompt,
)
from briocare.runtime.clock import WallClock
from briocare.runtime.events import (
    InputEvent,
    ParticipantSpoke,
    SilenceTimeout,
    StartSession,
    Tick,
)
from briocare.runtime.machine import SessionMachine
from briocare.runtime.state import Lifecycle
from briocare.scripts.schema import ExerciseScript
from server import protocol
from server.daily import Daily
from server.notes import NoteTaker
from server.phraser import Phraser

THERAPIST_NAME = "Therapist"
MAX_KIDS = 6
_NOTES_EVERY = 6  # refresh live notes after this many new utterances

# Spoken actions whose wording is friendly to share with the kids too.
_SHAREABLE = frozenset(
    {PromptSource.INTRO, PromptSource.PHASE_OPENING, PromptSource.PHASE_TRANSITION, PromptSource.INJECTED}
)


@dataclass
class KidConn:
    pid: str
    name: str
    ws: Any


class SessionRoom:
    def __init__(
        self,
        code: str,
        script: ExerciseScript,
        *,
        phraser: Phraser,
        notes: NoteTaker,
        daily: Daily,
    ) -> None:
        self.code = code
        self.script = script
        self.phraser = phraser
        self.notes = notes
        self.daily = daily

        self.machine: SessionMachine | None = None
        self.clock: WallClock | None = None
        self.therapist_ws: Any | None = None
        self.kids: dict[str, KidConn] = {}  # pid -> conn, in join order
        self._kid_seq = 0

        self.transcript: list[dict[str, Any]] = []
        self._utts_since_notes = 0
        self._notes_task: asyncio.Task[None] | None = None

        self.room_url: str | None = None
        self._room_fetched = False

        self.lock = asyncio.Lock()
        self.deadline_task: asyncio.Task[None] | None = None

    # -- socket lifecycle ---------------------------------------------------

    async def attach(self, role: str, ws: Any) -> bool:
        """Register a socket. Returns False if the therapist seat or kid capacity is full."""
        url = await self._ensure_room()
        if role == protocol.THERAPIST:
            if self.therapist_ws is not None:
                return False
            self.therapist_ws = ws
            await self._send(ws, protocol.room_info_msg(url))
            await self._send(ws, protocol.snapshot_msg(self._snapshot()))
            if self.machine is None:
                await self._send(ws, protocol.notice_msg("Waiting for kids to join, then press Start."))
            return True

        # kid
        if len(self.kids) >= MAX_KIDS:
            return False
        self._kid_seq += 1
        pid = f"kid{self._kid_seq}"
        conn = KidConn(pid=pid, name=f"Kid {self._kid_seq}", ws=ws)
        self.kids[pid] = conn
        await self._send(ws, protocol.room_info_msg(url))
        await self._send(ws, protocol.identity_msg(pid=pid, name=conn.name))
        await self._send(ws, protocol.snapshot_msg(self._snapshot()))
        if self.machine is not None:
            await self._send(ws, protocol.notice_msg("The session is already in progress — you're observing."))
        await self._send_therapist(protocol.snapshot_msg(self._snapshot()))
        return True

    async def detach(self, role: str, ws: Any) -> None:
        if role == protocol.THERAPIST and self.therapist_ws is ws:
            self.therapist_ws = None
            return
        conn = self._conn_for_ws(ws)
        if conn is not None:
            self.kids.pop(conn.pid, None)
            await self._send_therapist(protocol.snapshot_msg(self._snapshot()))

    async def _ensure_room(self) -> str | None:
        if not self._room_fetched:
            self.room_url = await self.daily.get_or_create_room(self.code)
            self._room_fetched = True
        return self.room_url

    def _conn_for_ws(self, ws: Any) -> KidConn | None:
        return next((c for c in self.kids.values() if c.ws is ws), None)

    # -- inbound messages ---------------------------------------------------

    async def handle_client_message(self, role: str, ws: Any, raw: str) -> None:
        try:
            msg = protocol.parse_client_message(raw)
        except protocol.ProtocolError:
            return
        if not self._role_allows(role, msg):
            return
        if isinstance(msg, protocol.JoinMsg):
            await self._on_join(ws, msg.name)
            return
        if isinstance(msg, protocol.SpokeMsg):
            await self._on_spoke(role, ws, msg.text)
            return
        if isinstance(msg, protocol.StartMsg):
            await self._on_start()
            return
        await self._feed(protocol.to_event(msg))

    @staticmethod
    def _role_allows(role: str, msg: protocol.ClientMessage) -> bool:
        if role == protocol.KID:
            return isinstance(msg, (protocol.JoinMsg, protocol.SpokeMsg, protocol.EndMsg))
        return isinstance(msg, (protocol.StartMsg, protocol.SpokeMsg, protocol.OverrideMsg, protocol.EndMsg))

    async def _on_join(self, ws: Any, name: str) -> None:
        conn = self._conn_for_ws(ws)
        if conn is None:
            return
        name = name.strip()
        if name:
            conn.name = name[:40]
        await self._send(ws, protocol.identity_msg(pid=conn.pid, name=conn.name))
        await self._send_therapist(protocol.snapshot_msg(self._snapshot()))

    async def _on_start(self) -> None:
        if not self.kids:
            await self._send_therapist(protocol.notice_msg("No kids have joined yet."))
            return
        if self.machine is not None:
            return
        roster = {c.pid: c.name for c in self.kids.values()}  # join order = turn order
        await self._feed(StartSession(at=0.0, roster=roster))

    async def _on_spoke(self, role: str, ws: Any, text: str) -> None:
        text = text.strip()
        if not text:
            return
        if role == protocol.KID:
            conn = self._conn_for_ws(ws)
            if conn is None:
                return
            pid, name = conn.pid, conn.name
        else:
            pid, name = None, THERAPIST_NAME

        at = self.clock.now() if self.clock is not None else 0.0
        self.transcript.append({"role": role, "name": name, "pid": pid, "text": text, "at": at})
        await self._send_therapist(protocol.transcript_msg(role=role, name=name, text=text, at=at))

        # Only a rostered kid's words drive the session-mechanics engine.
        if (
            pid is not None
            and self.machine is not None
            and self.machine.state.lifecycle != Lifecycle.ENDED
            and pid in self.machine.state.roster
        ):
            await self._feed(ParticipantSpoke(at=0.0, participant_id=pid, text=text))

        self._utts_since_notes += 1
        if self._utts_since_notes >= _NOTES_EVERY:
            self._trigger_notes()

    # -- the core engine step ----------------------------------------------

    async def _feed(self, event: InputEvent) -> None:
        async with self.lock:
            if self.machine is None:
                if not isinstance(event, StartSession):
                    return
                self.clock = WallClock()
                self.machine = SessionMachine(self.script, self.clock)
            assert self.clock is not None and self.machine is not None

            stamped = event.model_copy(update={"at": self.clock.now()})
            actions = self.machine.step(stamped)
            await self._broadcast_actions(actions)
            self._reschedule_deadline()

    async def _broadcast_actions(self, actions: list[Any]) -> None:
        assert self.machine is not None
        roster = dict(self.machine.state.roster)

        # Therapist: full action/cue feed + live state snapshot.
        actions_json = [a.model_dump(mode="json") for a in actions]
        lines = [render_action(a, roster) for a in actions]
        await self._send_therapist(protocol.actions_msg(actions_json, lines))

        snapshot = self._snapshot()
        await self._broadcast(protocol.snapshot_msg(snapshot))

        # Shared, kid-appropriate prompts (activity openings/transitions), optionally phrased.
        notes_phase = self._current_phase_notes()
        history = list(self.machine.state.history)
        advanced = False
        for action in actions:
            if isinstance(action, SayPrompt) and action.source in _SHAREABLE:
                text = await self.phraser.phrase(action, facilitator_notes=notes_phase, recent_history=history)
                if text:
                    await self._broadcast(protocol.assistant_msg(text))
            if isinstance(action, (AcknowledgeSpeaker, InviteParticipant)):
                advanced = True
            if isinstance(action, EndSession):
                await self._broadcast(protocol.session_over_msg())
                self._trigger_notes(final=True)
        if advanced:
            self._trigger_notes()

    # -- real-time deadline driving (unchanged scaffolding) ----------------

    def _reschedule_deadline(self) -> None:
        if self.deadline_task is not None:
            self.deadline_task.cancel()
            self.deadline_task = None
        if self.machine is None:
            return
        target = self.machine.next_deadline()
        if target is None:
            return
        self.deadline_task = asyncio.create_task(self._fire_deadline_at(target))

    async def _fire_deadline_at(self, target: float) -> None:
        assert self.clock is not None
        with contextlib.suppress(asyncio.CancelledError):
            delay = max(0.0, target - self.clock.now())
            await asyncio.sleep(delay)
            await self._feed(self._deadline_event())

    def _deadline_event(self) -> InputEvent:
        assert self.machine is not None and self.clock is not None
        st = self.machine.state.phase
        at = self.clock.now()
        if st is not None and st.current_turn is not None:
            return SilenceTimeout(at=at)
        return Tick(at=at)

    # -- AI notes (debounced, off the lock) --------------------------------

    def _trigger_notes(self, *, final: bool = False) -> None:
        if not final and self._notes_task is not None and not self._notes_task.done():
            return
        self._utts_since_notes = 0
        self._notes_task = asyncio.create_task(self._run_notes(final=final))

    async def _run_notes(self, *, final: bool) -> None:
        snapshot = list(self.transcript)
        if not snapshot:
            return
        markdown = await (self.notes.summary(snapshot) if final else self.notes.update(snapshot))
        if markdown:
            await self._send_therapist(protocol.notes_msg(markdown, final=final))

    # -- snapshots & sends --------------------------------------------------

    def _current_phase_notes(self) -> str | None:
        if self.machine is None or self.machine.state.phase is None:
            return None
        with contextlib.suppress(KeyError):
            return self.script.phase_by_id(self.machine.state.phase.phase_id).facilitator_notes
        return None

    def _snapshot(self) -> dict:
        lobby = [{"pid": c.pid, "name": c.name} for c in self.kids.values()]
        if self.machine is None:
            return {
                "lifecycle": Lifecycle.NOT_STARTED.value,
                "phase_id": None,
                "phase_title": None,
                "phase_index": -1,
                "current_turn": None,
                "current_turn_name": None,
                "paused": False,
                "agent_muted": False,
                "participants": [],
                "lobby": lobby,
            }
        state = self.machine.state
        roster = state.roster
        phase = state.phase
        phase_title = None
        if phase is not None:
            with contextlib.suppress(KeyError):
                phase_title = self.script.phase_by_id(phase.phase_id).title
        current_turn = phase.current_turn if phase is not None else None
        participants = []
        if phase is not None:
            for pid, name in roster.items():
                ps = phase.per_participant.get(pid)
                participants.append(
                    {
                        "pid": pid,
                        "name": name,
                        "spoke_count": ps.spoke_count if ps else 0,
                        "passed": ps.passed if ps else False,
                        "skipped": ps.skipped if ps else False,
                        "invites_received": ps.invites_received if ps else 0,
                    }
                )
        return {
            "lifecycle": state.lifecycle.value,
            "phase_id": phase.phase_id if phase is not None else None,
            "phase_title": phase_title,
            "phase_index": state.phase_index,
            "current_turn": current_turn,
            "current_turn_name": roster.get(current_turn) if current_turn else None,
            "paused": state.paused,
            "agent_muted": state.agent_muted,
            "participants": participants,
            "lobby": lobby,
        }

    async def _send(self, ws: Any | None, obj: dict) -> None:
        if ws is None:
            return
        with contextlib.suppress(Exception):
            await ws.send_json(obj)

    async def _send_kids(self, obj: dict) -> None:
        for conn in list(self.kids.values()):
            await self._send(conn.ws, obj)

    async def _send_therapist(self, obj: dict) -> None:
        await self._send(self.therapist_ws, obj)

    async def _broadcast(self, obj: dict) -> None:
        await self._send_kids(obj)
        await self._send_therapist(obj)
