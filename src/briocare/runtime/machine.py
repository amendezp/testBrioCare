"""The session state machine.

Synchronous, deterministic, side-effect free: feed it an :class:`InputEvent`
via :meth:`step` and it returns an ordered list of :class:`FacilitatorAction`.
A :class:`~briocare.io.facilitator_sink.FacilitatorSink` realizes the actions.
"""

from __future__ import annotations

from collections.abc import Iterable

from briocare.runtime.actions import (
    AcknowledgeSpeaker,
    AdvancePhase,
    EndSession,
    FacilitatorAction,
    InviteParticipant,
    InviteReason,
    NoOp,
    PromptSource,
    RequestRating,
    SayPrompt,
    SuggestEcho,
    WrapUpPhase,
    order_actions,
)
from briocare.runtime.clock import Clock, LogicalClock
from briocare.runtime.events import (
    ClinicianOverride,
    EndSessionRequest,
    InputEvent,
    OverrideCommand,
    ParticipantRated,
    ParticipantSpoke,
    SilenceTimeout,
    StartSession,
    Tick,
)
from briocare.runtime.policies import (
    all_participants_done,
    all_participants_rated,
    next_turn,
    phase_complete,
    quiet_candidates,
    rank_by_need,
    should_warn_wrapup,
)
from briocare.runtime.state import Lifecycle, ParticipantPhaseState, PhaseRuntimeState, SessionState
from briocare.scripts.schema import (
    ExerciseScript,
    Phase,
    QuietStrategy,
    TurnOrder,
)

DEFAULT_PASS_TOKENS = ("pass", "<pass>")

_MANAGED_TURN_ORDERS = frozenset({TurnOrder.ROUND_ROBIN, TurnOrder.FACILITATOR_PICK})
_SPOKEN_ACTION_TYPES = (SayPrompt, InviteParticipant, AcknowledgeSpeaker)


class SessionMachine:
    def __init__(
        self,
        script: ExerciseScript,
        clock: Clock | None = None,
        *,
        pass_tokens: Iterable[str] = DEFAULT_PASS_TOKENS,
    ) -> None:
        self.script = script
        self.clock: Clock = clock if clock is not None else LogicalClock()
        self.pass_tokens = {t.strip().lower() for t in pass_tokens}
        self.state = SessionState(script_id=script.id)
        self._now: float = 0.0
        self._action_seq = 0

    # -- public API ---------------------------------------------------------

    @property
    def roster_order(self) -> list[str]:
        return list(self.state.roster.keys())

    def step(self, event: InputEvent) -> list[FacilitatorAction]:
        now = float(event.at)
        self._now = now
        self.state.record(now, "event", event)
        raw = self._dispatch(event, now)
        ordered = order_actions([self._maybe_mute(a, now) for a in raw])
        actions: list[FacilitatorAction] = []
        for a in ordered:
            self._action_seq += 1
            stamped = a.model_copy(update={"action_id": f"a{self._action_seq}"})
            self.state.record(stamped.at, "action", stamped)
            actions.append(stamped)  # type: ignore[arg-type]
        return actions

    def next_deadline(self) -> float | None:
        st = self.state.phase
        if self.state.lifecycle != Lifecycle.IN_PHASE or st is None:
            return None
        phase = self._current_phase()
        cands: list[float] = [st.entered_at + phase.pacing.min_phase_seconds]
        if st.current_turn is not None and st.turn_started_at is not None:
            cands.append(st.turn_started_at + phase.turn_policy.per_turn_hard_seconds)
        pacing = phase.pacing
        if pacing.max_phase_seconds is not None:
            if pacing.wrapup_warning_seconds is not None and not st.wrapup_warned:
                cands.append(st.entered_at + pacing.max_phase_seconds - pacing.wrapup_warning_seconds)
            cands.append(st.entered_at + pacing.max_phase_seconds)
        if self._has_pending_quiet_candidate(phase, st):
            cands.append(self._idle_since(st) + phase.participation.invite_quiet_after_seconds)
        future = [c for c in cands if c > self._now]
        return min(future) if future else None

    # -- dispatch -----------------------------------------------------------

    def _dispatch(self, event: InputEvent, now: float) -> list[FacilitatorAction]:
        if self.state.lifecycle == Lifecycle.ENDED:
            return [NoOp(at=now, reason="session ended")]
        if self.state.paused and not isinstance(event, (ClinicianOverride, EndSessionRequest)):
            return [NoOp(at=now, reason="paused")]
        if isinstance(event, StartSession):
            return self._on_start(event, now)
        if isinstance(event, ParticipantSpoke):
            return self._on_spoke(event, now)
        if isinstance(event, ParticipantRated):
            return self._on_rated(event, now)
        if isinstance(event, (Tick, SilenceTimeout)):
            return self._on_tick(now)
        if isinstance(event, ClinicianOverride):
            return self._on_override(event, now)
        if isinstance(event, EndSessionRequest):
            return self._end_session(now, with_closing=True)
        return [NoOp(at=now, reason="unhandled event")]

    # -- event handlers -----------------------------------------------------

    def _on_start(self, event: StartSession, now: float) -> list[FacilitatorAction]:
        if self.state.lifecycle != Lifecycle.NOT_STARTED:
            return [NoOp(at=now, reason="session already started")]
        self.state.roster = dict(event.roster)
        self.state.lifecycle = Lifecycle.INTRO
        actions: list[FacilitatorAction] = []
        if self.script.intro_prompt is not None:
            actions.append(SayPrompt(at=now, source=PromptSource.INTRO, text=self.script.intro_prompt.text))
        actions.extend(self._enter_phase(0, now))
        return actions

    def _on_spoke(self, event: ParticipantSpoke, now: float) -> list[FacilitatorAction]:
        st = self.state.phase
        if self.state.lifecycle != Lifecycle.IN_PHASE or st is None:
            return [NoOp(at=now, reason="no active phase")]
        pid = event.participant_id
        if pid not in self.state.roster:
            return [NoOp(at=now, reason=f"unknown participant {pid!r}")]
        phase = self._current_phase()
        pps = st.ps(pid)
        is_pass = phase.turn_policy.allow_pass and event.text.strip().lower() in self.pass_tokens
        self.state.last_any_speech_at = now
        was_current = st.current_turn == pid
        actions: list[FacilitatorAction] = []
        if is_pass:
            pps.passed = True
        else:
            pps.spoke_count += 1
            pps.last_spoke_at = now
            self.state.contributions[pid] = self.state.contributions.get(pid, 0) + 1
            # A share is "spontaneous" when it isn't a facilitator-assigned managed turn —
            # an open/popcorn share, or speaking up out of turn. Rising spontaneity marks the
            # radial->peer shift that signals group-therapy progress (Arias-Pujol & Anguera, 2017).
            if phase.turn_policy.order not in _MANAGED_TURN_ORDERS or not was_current:
                self.state.spontaneous[pid] = self.state.spontaneous.get(pid, 0) + 1
            # Echo cue: a previously-nudged child finally spoke — suggest the therapist echo them.
            if pps.invites_received > 0 and not pps.echoed:
                pps.echoed = True
                actions.append(SuggestEcho(at=now, participant_id=pid, text=event.text))
            if phase.acknowledge_speakers:
                name = self.state.roster[pid]
                actions.append(AcknowledgeSpeaker(at=now, participant_id=pid, text=f"Thank you, {name}."))

        if phase_complete(phase, st, self.roster_order, now):
            actions.extend(self._complete_phase(now))
            return actions
        if phase.turn_policy.order in _MANAGED_TURN_ORDERS and (was_current or st.current_turn is None):
            self._advance_turn(st, phase, now)
            if st.current_turn is not None:
                actions.append(self._invite(st.current_turn, InviteReason.ROUND_ROBIN_TURN, now))
        return actions

    def _on_rated(self, event: ParticipantRated, now: float) -> list[FacilitatorAction]:
        st = self.state.phase
        if self.state.lifecycle != Lifecycle.IN_PHASE or st is None:
            return [NoOp(at=now, reason="no active phase")]
        phase = self._current_phase()
        if phase.mode != "rating":
            return [NoOp(at=now, reason="not a rating phase")]
        pid = event.participant_id
        if pid not in self.state.roster:
            return [NoOp(at=now, reason=f"unknown participant {pid!r}")]
        pps = st.ps(pid)
        pps.rating = event.value
        self.state.ratings.setdefault(phase.id, {})[pid] = event.value
        self.state.last_any_speech_at = now
        actions: list[FacilitatorAction] = []
        if all_participants_rated(st, self.roster_order):
            actions.extend(self._complete_phase(now))  # everyone tapped — advance early
        return actions

    def _on_tick(self, now: float) -> list[FacilitatorAction]:
        st = self.state.phase
        if self.state.lifecycle != Lifecycle.IN_PHASE or st is None:
            return []
        phase = self._current_phase()
        actions: list[FacilitatorAction] = []

        # 1. turn hard cap exceeded -> move the turn on
        if (
            st.current_turn is not None
            and st.turn_started_at is not None
            and now - st.turn_started_at >= phase.turn_policy.per_turn_hard_seconds
        ):
            self._advance_turn(st, phase, now)
            if st.current_turn is not None:
                actions.append(self._invite(st.current_turn, InviteReason.ROUND_ROBIN_TURN, now))

        # 2. phase complete (timer / all-spoke)
        if phase_complete(phase, st, self.roster_order, now):
            actions.extend(self._complete_phase(now))
            return actions

        # 3. wrap-up warning (once)
        if should_warn_wrapup(phase, st, now):
            st.wrapup_warned = True
            actions.append(
                SayPrompt(
                    at=now,
                    source=PromptSource.WRAPUP_WARNING,
                    text="Let's start wrapping up this part — about a minute to go.",
                    phase_id=phase.id,
                )
            )
            return actions

        # 4. quiet nudge — surface the most-inhibited eligible child first (need-weighted)
        cands = quiet_candidates(phase, st, self.roster_order, now, self._idle_since(st))
        if cands:
            target = rank_by_need(cands, self.state.contributions, st, self.roster_order)[0]
            tps = st.ps(target)
            tps.invites_received += 1
            st.last_nudge_at = now
            actions.append(
                self._invite(
                    target,
                    InviteReason.QUIET_NUDGE,
                    now,
                    attempt=tps.invites_received,
                    max_attempts=phase.participation.max_invites_per_participant,
                )
            )
        return actions

    def _on_override(self, event: ClinicianOverride, now: float) -> list[FacilitatorAction]:
        cmd = event.command
        args = event.args
        actions: list[FacilitatorAction] = [NoOp(at=now, reason=f"clinician override: {cmd.value}")]
        st = self.state.phase
        phase = self._current_phase() if (st is not None and self.state.phase_index >= 0) else None

        if cmd == OverrideCommand.ADVANCE_PHASE:
            if self.state.lifecycle == Lifecycle.IN_PHASE:
                actions.extend(self._complete_phase(now))
        elif cmd == OverrideCommand.GOTO_PHASE:
            target = args.get("phase_id", "")
            try:
                idx = self.script.phase_index(target)
            except KeyError:
                return [NoOp(at=now, reason=f"no such phase {target!r}")]
            if st is not None:
                actions.append(AdvancePhase(at=now, from_phase=st.phase_id, to_phase=target))
            actions.extend(self._enter_phase(idx, now))
        elif cmd == OverrideCommand.SET_TURN:
            pid = args.get("pid", "")
            if st is not None and phase is not None and pid in self.state.roster:
                st.current_turn = pid
                st.turn_started_at = now
                if pid in self.roster_order:
                    st.order_cursor = self.roster_order.index(pid)
                actions.append(self._invite(pid, InviteReason.CLINICIAN_DIRECTED, now))
        elif cmd == OverrideCommand.SKIP_PARTICIPANT:
            pid = args.get("pid", "")
            if st is not None and phase is not None and pid in self.state.roster:
                st.ps(pid).skipped = True
                if st.current_turn == pid:
                    self._advance_turn(st, phase, now)
                    if st.current_turn is not None:
                        actions.append(self._invite(st.current_turn, InviteReason.ROUND_ROBIN_TURN, now))
                if phase_complete(phase, st, self.roster_order, now):
                    actions.extend(self._complete_phase(now))
        elif cmd == OverrideCommand.PAUSE:
            self.state.paused = True
            if self.state.lifecycle == Lifecycle.IN_PHASE:
                self.state.lifecycle = Lifecycle.PAUSED
        elif cmd == OverrideCommand.RESUME:
            self.state.paused = False
            if self.state.lifecycle == Lifecycle.PAUSED:
                self.state.lifecycle = Lifecycle.IN_PHASE
        elif cmd == OverrideCommand.MUTE_AGENT:
            self.state.agent_muted = True
        elif cmd == OverrideCommand.UNMUTE_AGENT:
            self.state.agent_muted = False
        elif cmd == OverrideCommand.INJECT_PROMPT:
            actions.append(SayPrompt(at=now, source=PromptSource.INJECTED, text=args.get("text", "")))
        elif cmd == OverrideCommand.END_SESSION:
            actions.extend(self._end_session(now, with_closing=True))
        return actions

    # -- phase transitions --------------------------------------------------

    def _enter_phase(self, index: int, now: float) -> list[FacilitatorAction]:
        if index >= len(self.script.phases):
            return self._close_session(now)
        self.state.phase_index = index
        phase = self.script.phases[index]
        self.state.lifecycle = Lifecycle.IN_PHASE
        st = PhaseRuntimeState(
            phase_id=phase.id,
            entered_at=now,
            per_participant={pid: ParticipantPhaseState() for pid in self.roster_order},
        )
        self.state.phase = st
        if phase.mode == "rating":
            # No managed speaking turns — each child taps a feelings-thermometer value.
            return [RequestRating(at=now, scale=phase.rating_scale, prompt_text=phase.opening_prompt.text)]
        actions: list[FacilitatorAction] = [
            SayPrompt(at=now, source=PromptSource.PHASE_OPENING, text=phase.opening_prompt.text, phase_id=phase.id)
        ]
        nt = next_turn(
            phase.turn_policy.order,
            self.roster_order,
            st,
            start=0,
            one_turn=phase.turn_policy.one_turn_per_participant,
        )
        if nt is not None:
            st.current_turn = nt
            st.turn_started_at = now
            st.order_cursor = self.roster_order.index(nt)
            actions.append(self._invite(nt, InviteReason.ROUND_ROBIN_TURN, now))
        return actions

    def _complete_phase(self, now: float) -> list[FacilitatorAction]:
        leaving = self._current_phase()
        actions: list[FacilitatorAction] = [WrapUpPhase(at=now, phase_id=leaving.id)]
        next_index = self.state.phase_index + 1
        has_next = next_index < len(self.script.phases)
        to_id = self.script.phases[next_index].id if has_next else None
        if leaving.transition_prompt is not None:
            actions.append(
                SayPrompt(
                    at=now,
                    source=PromptSource.PHASE_TRANSITION,
                    text=leaving.transition_prompt.text,
                    phase_id=leaving.id,
                )
            )
        actions.append(AdvancePhase(at=now, from_phase=leaving.id, to_phase=to_id))
        self.state.lifecycle = Lifecycle.BETWEEN_PHASES
        if has_next:
            actions.extend(self._enter_phase(next_index, now))
        else:
            actions.extend(self._close_session(now))
        return actions

    def _close_session(self, now: float) -> list[FacilitatorAction]:
        self.state.lifecycle = Lifecycle.CLOSING
        actions: list[FacilitatorAction] = []
        if self.script.closing_prompt is not None:
            actions.append(SayPrompt(at=now, source=PromptSource.CLOSING, text=self.script.closing_prompt.text))
        actions.append(EndSession(at=now))
        self.state.lifecycle = Lifecycle.ENDED
        self.state.phase = None
        return actions

    def _end_session(self, now: float, *, with_closing: bool) -> list[FacilitatorAction]:
        if self.state.lifecycle == Lifecycle.ENDED:
            return [NoOp(at=now, reason="session already ended")]
        actions: list[FacilitatorAction] = []
        speaking_states = {Lifecycle.INTRO, Lifecycle.IN_PHASE, Lifecycle.BETWEEN_PHASES}
        if with_closing and self.script.closing_prompt is not None and self.state.lifecycle in speaking_states:
            actions.append(SayPrompt(at=now, source=PromptSource.CLOSING, text=self.script.closing_prompt.text))
        actions.append(EndSession(at=now))
        self.state.lifecycle = Lifecycle.ENDED
        self.state.phase = None
        return actions

    # -- helpers ------------------------------------------------------------

    def _current_phase(self) -> Phase:
        return self.script.phases[self.state.phase_index]

    def _advance_turn(self, st: PhaseRuntimeState, phase: Phase, now: float) -> None:
        if st.current_turn is not None and st.current_turn in self.roster_order:
            start = self.roster_order.index(st.current_turn) + 1
        else:
            start = st.order_cursor
        nt = next_turn(
            phase.turn_policy.order,
            self.roster_order,
            st,
            start=start,
            one_turn=phase.turn_policy.one_turn_per_participant,
        )
        st.current_turn = nt
        if nt is not None:
            st.turn_started_at = now
            st.order_cursor = self.roster_order.index(nt)
        else:
            st.turn_started_at = None

    def _invite(
        self,
        pid: str,
        reason: InviteReason,
        now: float,
        *,
        attempt: int | None = None,
        max_attempts: int | None = None,
    ) -> InviteParticipant:
        name = self.state.roster.get(pid, pid)
        if reason == InviteReason.ROUND_ROBIN_TURN:
            text = f"{name}, it's your turn — how are you feeling?"
        elif reason == InviteReason.QUIET_NUDGE:
            strat = self._current_phase().participation.quiet_participant_strategy
            if strat == QuietStrategy.GENTLE_OPEN_INVITE:
                text = "If there's anyone who hasn't shared yet, you're welcome to."
            else:
                text = f"{name}, would you like to share how you're feeling?"
        else:  # CLINICIAN_DIRECTED
            text = f"{name}, would you like to add something?"
        return InviteParticipant(
            at=now,
            participant_id=pid,
            text=text,
            reason=reason,
            attempt=attempt,
            max_attempts=max_attempts,
        )

    def _maybe_mute(self, action: FacilitatorAction, now: float) -> FacilitatorAction:
        if not self.state.agent_muted:
            return action
        if isinstance(action, SayPrompt) and action.source == PromptSource.INJECTED:
            return action
        if isinstance(action, _SPOKEN_ACTION_TYPES):
            summary = self._action_summary(action)
            return NoOp(at=now, reason=f"agent_muted: would have said {summary}")
        return action

    @staticmethod
    def _action_summary(action: FacilitatorAction) -> str:
        if isinstance(action, SayPrompt):
            return f"({action.source.value}) {action.text!r}"
        if isinstance(action, InviteParticipant):
            return f"invite {action.participant_id} ({action.reason.value})"
        if isinstance(action, AcknowledgeSpeaker):
            return f"ack {action.participant_id}"
        return action.kind

    def _idle_since(self, st: PhaseRuntimeState) -> float:
        candidates = [st.entered_at]
        if self.state.last_any_speech_at is not None:
            candidates.append(self.state.last_any_speech_at)
        if st.last_nudge_at is not None:
            candidates.append(st.last_nudge_at)
        return max(candidates)

    def _has_pending_quiet_candidate(self, phase: Phase, st: PhaseRuntimeState) -> bool:
        pol = phase.participation
        if not pol.require_all_speak or pol.quiet_participant_strategy == QuietStrategy.SKIP:
            return False
        if all_participants_done(st, self.roster_order):
            return False
        cap = phase.participation.max_invites_per_participant
        for pid in self.roster_order:
            ps = st.per_participant.get(pid)
            if ps is not None and (ps.spoke_count > 0 or ps.passed or ps.skipped):
                continue
            if (ps.invites_received if ps else 0) < cap:
                return True
        return False
