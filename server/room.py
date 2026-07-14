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
from datetime import datetime
from typing import Any

from briocare.io.facilitator_sink import render_action
from briocare.runtime.actions import (
    AcknowledgeSpeaker,
    AdvancePhase,
    EndSession,
    InviteParticipant,
    InviteReason,
    PromptSource,
    RequestRating,
    SayPrompt,
    SuggestEcho,
)
from briocare.runtime.clock import WallClock
from briocare.runtime.events import (
    InputEvent,
    ParticipantRated,
    ParticipantSpoke,
    SilenceTimeout,
    StartSession,
    Tick,
)
from briocare.runtime.machine import SessionMachine
from briocare.runtime.state import Lifecycle
from briocare.scripts.schema import ExerciseScript
from server import dump, privacy, protocol
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
        self.started_at: datetime | None = None
        self.therapist_ws: Any | None = None
        self.kids: dict[str, KidConn] = {}  # pid -> conn, in join order
        self._kid_seq = 0

        self.transcript: list[dict[str, Any]] = []
        self._utts_since_notes = 0
        self._notes_task: asyncio.Task[None] | None = None
        self.last_notes = ""

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
            if self.machine is not None and self.machine.state.lifecycle != Lifecycle.ENDED:
                await self._send_therapist(protocol.notice_msg(f"{conn.name} left the session."))
            await self._send_therapist(protocol.snapshot_msg(self._snapshot()))

    async def _ensure_room(self) -> str | None:
        if not self._room_fetched:
            self.room_url = await self.daily.get_or_create_room(self.code)
            # Latch only on success (or when video is disabled, so there's nothing to retry).
            # A transient Daily error then leaves video off forever for the room otherwise.
            if self.room_url is not None or not self.daily.enabled:
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
        if isinstance(msg, protocol.QuickReplyMsg):
            await self._on_quick_reply(ws, msg.text)
            return
        if isinstance(msg, protocol.RatingMsg):
            await self._on_rating(ws, msg.value)
            return
        if isinstance(msg, protocol.PrivateNudgeMsg):
            await self._on_private_nudge(msg.pid)
            return
        if isinstance(msg, protocol.StartMsg):
            await self._on_start()
            return
        await self._feed(protocol.to_event(msg))

    @staticmethod
    def _role_allows(role: str, msg: protocol.ClientMessage) -> bool:
        if role == protocol.KID:
            # A child can join, speak, tap a chip, and rate — but never end the group
            # session (they leave by closing their own tab; the therapist ends the session).
            return isinstance(
                msg,
                (protocol.JoinMsg, protocol.SpokeMsg, protocol.QuickReplyMsg, protocol.RatingMsg),
            )
        return isinstance(
            msg,
            (protocol.StartMsg, protocol.SpokeMsg, protocol.PrivateNudgeMsg, protocol.OverrideMsg, protocol.EndMsg),
        )

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
        # join order = turn order; de-duplicate names so per-child name redaction is unambiguous
        roster = privacy.dedupe_names({c.pid: c.name for c in self.kids.values()})
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
        await self._send_therapist(protocol.transcript_msg(role=role, name=name, text=text, at=at, pid=pid))

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

    async def _on_quick_reply(self, ws: Any, text: str) -> None:
        """A child tapped a feeling chip. Auto-relay it to the whole circle (the child
        opted in by tapping) and count it as their contribution / managed turn."""
        text = text.strip()[:80]
        if not text:
            return
        conn = self._conn_for_ws(ws)
        if conn is None:
            return
        pid, name = conn.pid, conn.name
        at = self.clock.now() if self.clock is not None else 0.0
        self.transcript.append(
            {"role": protocol.KID, "name": name, "pid": pid, "text": text, "at": at, "kind": "quick_reply"}
        )
        await self._send_therapist(
            protocol.transcript_msg(role=protocol.KID, name=name, text=f"(tapped) {text}", at=at, pid=pid)
        )
        await self._broadcast(protocol.assistant_msg(f"{name} wants us to know: {text}"))
        if (
            self.machine is not None
            and self.machine.state.lifecycle != Lifecycle.ENDED
            and pid in self.machine.state.roster
        ):
            await self._feed(ParticipantSpoke(at=0.0, participant_id=pid, text=text))
        self._utts_since_notes += 1
        if self._utts_since_notes >= _NOTES_EVERY:
            self._trigger_notes()

    async def _on_rating(self, ws: Any, value: int) -> None:
        """A child submitted a feelings-thermometer value during a rating phase."""
        conn = self._conn_for_ws(ws)
        if conn is None or self.machine is None:
            return
        pid, name = conn.pid, conn.name
        if self.machine.state.lifecycle == Lifecycle.ENDED or pid not in self.machine.state.roster:
            return
        # Only honour ratings during an active rating phase (ignore stray/late taps).
        phase = self.machine.state.phase
        if phase is None:
            return
        try:
            p = self.script.phase_by_id(phase.phase_id)
        except KeyError:
            return
        if p.mode != "rating":
            return
        label, scale = p.title, p.rating_scale
        value = max(1, min(scale, int(value)))
        at = self.clock.now() if self.clock is not None else 0.0
        line = f"{label}: {value}/{scale}"
        self.transcript.append(
            {"role": protocol.KID, "name": name, "pid": pid, "text": line, "at": at, "kind": "rating"}
        )
        await self._send_therapist(
            protocol.transcript_msg(role=protocol.KID, name=name, text=line, at=at, pid=pid)
        )
        await self._feed(ParticipantRated(at=0.0, participant_id=pid, value=value))

    async def _on_private_nudge(self, pid: str) -> None:
        """Therapist-triggered: send one child gentle, one-directional encouragement."""
        conn = self.kids.get(pid)
        name = conn.name if conn is not None else (
            self.machine.state.roster.get(pid) if self.machine is not None else None
        )
        if name is None:
            return
        await self._send_kid(
            pid, protocol.private_prompt_msg(f"Take your time, {name} — we're glad you're here. \U0001f49b")
        )
        at = self.clock.now() if self.clock is not None else 0.0
        note = f"(privately encouraged {name})"
        self.transcript.append(
            {"role": protocol.THERAPIST, "name": THERAPIST_NAME, "pid": None, "text": note, "at": at,
             "kind": "private_nudge"}
        )
        await self._send_therapist(
            protocol.transcript_msg(role=protocol.THERAPIST, name=THERAPIST_NAME, text=note, at=at, pid=None)
        )

    # -- the core engine step ----------------------------------------------

    async def _feed(self, event: InputEvent) -> None:
        async with self.lock:
            if self.machine is None:
                if not isinstance(event, StartSession):
                    return
                self.clock = WallClock()
                self.started_at = datetime.now().astimezone()
                self.machine = SessionMachine(self.script, self.clock)
            assert self.clock is not None and self.machine is not None

            stamped = event.model_copy(update={"at": self.clock.now()})
            actions = self.machine.step(stamped)
            await self._broadcast_actions(actions)
            self._reschedule_deadline()

    async def _broadcast_actions(self, actions: list[Any]) -> None:
        assert self.machine is not None
        roster = dict(self.machine.state.roster)

        # Therapist: raw action log (kept for debugging/tests) + plain-language cues.
        actions_json = [a.model_dump(mode="json") for a in actions]
        lines = [render_action(a, roster) for a in actions]
        await self._send_therapist(protocol.actions_msg(actions_json, lines))
        cues = self._friendly_cues(actions, roster)
        if cues:
            await self._send_therapist(protocol.cues_msg(cues))

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
            if isinstance(action, RequestRating):
                await self._broadcast(protocol.request_rating_msg(scale=action.scale, prompt=action.prompt_text))
            if isinstance(action, (AcknowledgeSpeaker, InviteParticipant)):
                advanced = True
            if isinstance(action, EndSession):
                await self._broadcast(protocol.session_over_msg())
                self._trigger_final()
        if advanced:
            self._trigger_notes()

    def _friendly_cues(self, actions: list[Any], roster: dict[str, str]) -> list[dict]:
        """Turn a step's engine actions into plain, actionable therapist cues.

        Only actionable items survive; NoOp / acknowledgements / raw prompts are dropped.
        """

        def who(pid: str) -> str:
            return roster.get(pid, pid)

        def cue(icon: str, text: str, level: str, pid: str | None = None) -> None:
            cues.append({"icon": icon, "text": text, "level": level, "pid": pid})

        cues: list[dict] = []
        for a in actions:
            if isinstance(a, InviteParticipant):
                if a.reason == InviteReason.QUIET_NUDGE:
                    att = f" ({a.attempt}/{a.max_attempts})" if a.attempt else ""
                    cue("🔔", f"Invite {who(a.participant_id)} — quiet for a bit{att}", "action", a.participant_id)
                elif a.reason == InviteReason.ROUND_ROBIN_TURN:
                    cue("👉", f"{who(a.participant_id)}'s turn to share", "action", a.participant_id)
                else:
                    cue("👉", f"Over to {who(a.participant_id)}", "action", a.participant_id)
            elif isinstance(a, SuggestEcho):
                snippet = a.text if len(a.text) <= 60 else a.text[:57] + "…"
                cue("🔁", f'{who(a.participant_id)} opened up — try echoing it back: "{snippet}"',
                    "action", a.participant_id)
            elif isinstance(a, RequestRating):
                cues.append({"icon": "🌡️", "text": "Feelings check-in — kids are tapping", "level": "info"})
            elif isinstance(a, SayPrompt) and a.source == PromptSource.WRAPUP_WARNING:
                cues.append({"icon": "⏳", "text": "About a minute left in this activity", "level": "time"})
            elif isinstance(a, SayPrompt) and a.source == PromptSource.INTRO:
                cues.append({"icon": "🌱", "text": "Session started", "level": "info"})
            elif isinstance(a, AdvancePhase) and a.to_phase:
                title = a.to_phase
                with contextlib.suppress(KeyError):
                    title = self.script.phase_by_id(a.to_phase).title
                cues.append({"icon": "✅", "text": f"Next: {title}", "level": "info"})
            elif isinstance(a, EndSession):
                cues.append({"icon": "🏁", "text": "Session ended", "level": "info"})
        return cues

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

    def _trigger_notes(self) -> None:
        if self._notes_task is not None and not self._notes_task.done():
            return
        self._utts_since_notes = 0
        self._notes_task = asyncio.create_task(self._run_notes())

    async def _run_notes(self) -> None:
        snapshot = list(self.transcript)
        if not snapshot:
            return
        markdown = await self.notes.update(snapshot)
        if markdown:
            self.last_notes = markdown
            await self._send_therapist(protocol.notes_msg(markdown, final=False))

    def _trigger_final(self) -> None:
        """End-of-session: clinical note + per-child review (privacy-scoped parent
        summaries) for the therapist lobby + a fail-silent disk dump."""
        self._notes_task = asyncio.create_task(self._run_final())

    async def _run_final(self) -> None:
        transcript = list(self.transcript)
        final_md = await self.notes.summary(transcript) if transcript else ""
        if final_md:
            self.last_notes = final_md
            await self._send_therapist(protocol.notes_msg(final_md, final=True))
        kids = await self._build_kid_reviews(transcript)
        await self._send_therapist(protocol.session_review_msg(notes=final_md, kids=kids))
        self._dump_session(final_md, kids)

    async def _build_kid_reviews(self, transcript: list[dict[str, Any]]) -> list[dict]:
        """One review per rostered child: ratings, participation, that child's OWN
        transcript, and a privacy-scoped parent summary (built only from that child's
        own data + generic activity labels, then defensively redacted of any peer name)."""
        if self.machine is None:
            return []
        state = self.machine.state
        eng = self._engagement()
        activities = [p.title for p in self.script.phases]  # labels only — no children named
        # Activity-title tokens are script constants the model may echo ("Warm-up") —
        # allow them so the fail-closed validator doesn't drop good summaries.
        safe_tokens = privacy.title_tokens(activities)
        # Linear rating phases only — the on-demand (menu_only) thermometer must not be
        # mistaken for the closing check-out (mirrors _snapshot).
        rating_phase_ids = [p.id for p in self.script.phases if p.mode == "rating" and not p.menu_only]
        checkin_id = rating_phase_ids[0] if rating_phase_ids else None
        checkout_id = rating_phase_ids[-1] if len(rating_phase_ids) > 1 else None
        reviews: list[dict] = []
        for pid, name in state.roster.items():
            own = [e for e in transcript if e.get("pid") == pid]
            en = eng.get(pid, {})
            spont = state.spontaneous.get(pid, 0)
            participation = self._participation_note(name, en.get("utterances", 0), spont)
            others = [n for p, n in state.roster.items() if p != pid]
            # Privacy: scrub peers from the child's own words BEFORE generation, then fail
            # closed AFTER generation if any name/place-shaped token survives.
            summary = await self.notes.parent_summary_for_kid(
                kid_name=name,
                own_lines=privacy.scrub_own_lines([str(e.get("text", "")) for e in own], others=others),
                activities=activities,
                participation=participation,
            )
            summary = privacy.sanitize_summary(summary, others=others, keep=name, allow=safe_tokens)
            reviews.append(
                {
                    "pid": pid,
                    "name": name,
                    "rating_checkin": state.ratings.get(checkin_id, {}).get(pid) if checkin_id else None,
                    "rating_checkout": state.ratings.get(checkout_id, {}).get(pid) if checkout_id else None,
                    "utterances": en.get("utterances", 0),
                    "words": en.get("words", 0),
                    "spontaneous": spont,
                    "contributions": state.contributions.get(pid, 0),
                    "transcript": [
                        {"text": e.get("text", ""), "at": e.get("at"), "kind": e.get("kind", "")} for e in own
                    ],
                    "parent_summary": summary,
                }
            )
        return reviews

    @staticmethod
    def _participation_note(name: str, utterances: int, spontaneous: int) -> str:
        if utterances == 0:
            return f"{name} was quiet today and mostly listened."
        note = f"{name} shared {utterances} time" + ("s" if utterances != 1 else "")
        if spontaneous:
            note += f", including {spontaneous} time" + ("s" if spontaneous != 1 else "") + " speaking up unprompted"
        return note + "."

    def _dump_session(self, final_notes: str, kids: list[dict]) -> None:
        ratings = dict(self.machine.state.ratings) if self.machine is not None else {}
        roster = dict(self.machine.state.roster) if self.machine is not None else {}
        dump.dump_session(
            code=self.code,
            started_at=self.started_at.isoformat() if self.started_at else None,
            roster=roster,
            ratings=ratings,
            transcript=self.transcript,
            final_notes=final_notes,
            parent_summaries={k["pid"]: {"name": k["name"], "summary": k["parent_summary"]} for k in kids},
        )

    # -- snapshots & sends --------------------------------------------------

    def _current_phase_notes(self) -> str | None:
        if self.machine is None or self.machine.state.phase is None:
            return None
        with contextlib.suppress(KeyError):
            return self.script.phase_by_id(self.machine.state.phase.phase_id).facilitator_notes
        return None

    def _engagement(self) -> dict[str, dict]:
        """Session-total speaking stats per kid, derived from the transcript."""
        eng: dict[str, dict] = {}
        for e in self.transcript:
            if e.get("role") == protocol.KID and e.get("pid"):
                d = eng.setdefault(e["pid"], {"utterances": 0, "words": 0, "last_at": None})
                d["utterances"] += 1
                d["words"] += len(e.get("text", "").split())
                d["last_at"] = e.get("at")
        return eng

    def _snapshot(self) -> dict:
        lobby = [{"pid": c.pid, "name": c.name} for c in self.kids.values()]
        # The linear session is the non-menu_only phases; menu_only are on-demand activities.
        linear_ids = [p.id for p in self.script.phases if not p.menu_only]
        header = {
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "elapsed_seconds": self.clock.now() if self.clock is not None else 0.0,
            "activity_total": len(linear_ids),
            # On-demand activity library the therapist can launch by button.
            "activities": [{"id": p.id, "title": p.title} for p in self.script.phases if p.menu_only],
        }
        if self.machine is None:
            return {
                "lifecycle": Lifecycle.NOT_STARTED.value,
                "phase_id": None,
                "phase_title": None,
                "phase_mode": None,
                "phase_index": -1,
                "activity_index": -1,
                "current_turn": None,
                "current_turn_name": None,
                "paused": False,
                "agent_muted": False,
                "participants": [],
                "lobby": lobby,
                **header,
            }
        state = self.machine.state
        roster = state.roster
        phase = state.phase
        now = self.clock.now() if self.clock is not None else 0.0
        eng = self._engagement()
        phase_title = None
        phase_mode = None
        if phase is not None:
            with contextlib.suppress(KeyError):
                p = self.script.phase_by_id(phase.phase_id)
                phase_title, phase_mode = p.title, p.mode
        # First / last *linear* feelings-rating phases drive the check-in vs check-out trend
        # (the on-demand thermometer activity must not be mistaken for the closing check-out).
        rating_phase_ids = [p.id for p in self.script.phases if p.mode == "rating" and not p.menu_only]
        checkin_id = rating_phase_ids[0] if rating_phase_ids else None
        checkout_id = rating_phase_ids[-1] if len(rating_phase_ids) > 1 else None
        current_turn = phase.current_turn if phase is not None else None
        participants = []
        if phase is not None:
            for pid, name in roster.items():
                ps = phase.per_participant.get(pid)
                en = eng.get(pid, {})
                last_at = en.get("last_at")
                participants.append(
                    {
                        "pid": pid,
                        "name": name,
                        "spoke_count": ps.spoke_count if ps else 0,
                        "passed": ps.passed if ps else False,
                        "skipped": ps.skipped if ps else False,
                        "invites_received": ps.invites_received if ps else 0,
                        "utterances": en.get("utterances", 0),
                        "words": en.get("words", 0),
                        "last_spoke_ago": (now - last_at) if last_at is not None else None,
                        "spontaneous": state.spontaneous.get(pid, 0),
                        "rating_checkin": state.ratings.get(checkin_id, {}).get(pid) if checkin_id else None,
                        "rating_checkout": state.ratings.get(checkout_id, {}).get(pid) if checkout_id else None,
                    }
                )
        return {
            "lifecycle": state.lifecycle.value,
            "phase_id": phase.phase_id if phase is not None else None,
            "phase_title": phase_title,
            "phase_mode": phase_mode,
            "phase_index": state.phase_index,
            "activity_index": (
                linear_ids.index(phase.phase_id) if phase is not None and phase.phase_id in linear_ids else -1
            ),
            "current_turn": current_turn,
            "current_turn_name": roster.get(current_turn) if current_turn else None,
            "paused": state.paused,
            "agent_muted": state.agent_muted,
            "participants": participants,
            "lobby": lobby,
            **header,
        }

    async def _send(self, ws: Any | None, obj: dict) -> None:
        if ws is None:
            return
        with contextlib.suppress(Exception):
            await ws.send_json(obj)

    async def _send_kids(self, obj: dict) -> None:
        for conn in list(self.kids.values()):
            await self._send(conn.ws, obj)

    async def _send_kid(self, pid: str, obj: dict) -> None:
        """Send to exactly one child (private prompts must not reach the others)."""
        conn = self.kids.get(pid)
        if conn is not None:
            await self._send(conn.ws, obj)

    async def _send_therapist(self, obj: dict) -> None:
        await self._send(self.therapist_ws, obj)

    async def _broadcast(self, obj: dict) -> None:
        await self._send_kids(obj)
        await self._send_therapist(obj)
