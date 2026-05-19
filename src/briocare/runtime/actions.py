"""Facilitator actions returned by :meth:`SessionMachine.step`.

The machine performs no side effects — it returns these objects and a
:class:`~briocare.io.facilitator_sink.FacilitatorSink` realizes them.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class PromptSource(str, Enum):
    INTRO = "intro"
    PHASE_OPENING = "phase_opening"
    PHASE_TRANSITION = "phase_transition"
    CLOSING = "closing"
    WRAPUP_WARNING = "wrapup_warning"
    INJECTED = "injected"


class InviteReason(str, Enum):
    ROUND_ROBIN_TURN = "round_robin_turn"
    QUIET_NUDGE = "quiet_nudge"
    CLINICIAN_DIRECTED = "clinician_directed"


# Sources whose SayPrompt leads the action list at session / phase entry.
LEAD_SOURCES = frozenset(
    {PromptSource.INTRO, PromptSource.PHASE_OPENING, PromptSource.PHASE_TRANSITION}
)


class _Action(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    at: float
    action_id: str = Field(default_factory=lambda: uuid4().hex)


class SayPrompt(_Action):
    kind: Literal["say_prompt"] = "say_prompt"
    source: PromptSource
    text: str
    phase_id: str | None = None


class InviteParticipant(_Action):
    kind: Literal["invite_participant"] = "invite_participant"
    participant_id: str
    text: str
    reason: InviteReason
    attempt: int | None = None
    max_attempts: int | None = None


class AcknowledgeSpeaker(_Action):
    kind: Literal["acknowledge_speaker"] = "acknowledge_speaker"
    participant_id: str
    text: str | None = None


class AdvancePhase(_Action):
    kind: Literal["advance_phase"] = "advance_phase"
    from_phase: str
    to_phase: str | None = None


class WrapUpPhase(_Action):
    kind: Literal["wrap_up_phase"] = "wrap_up_phase"
    phase_id: str


class EndSession(_Action):
    kind: Literal["end_session"] = "end_session"


class NoOp(_Action):
    kind: Literal["no_op"] = "no_op"
    reason: str


FacilitatorAction = Annotated[
    SayPrompt | InviteParticipant | AcknowledgeSpeaker | AdvancePhase | WrapUpPhase | EndSession | NoOp,
    Field(discriminator="kind"),
]


_RANK: dict[str, int] = {
    "end_session": 1,
    "advance_phase": 2,
    "wrap_up_phase": 2,
    # say_prompt handled specially (lead vs. non-lead) in _rank()
    "acknowledge_speaker": 4,
    "invite_participant": 6,
    "no_op": 7,
}


def _rank(action: _Action) -> int:
    if isinstance(action, SayPrompt):
        return 3 if action.source in LEAD_SOURCES else 5
    kind: str = getattr(action, "kind")  # noqa: B009 - subclasses define `kind`
    return _RANK[kind]


def order_actions(actions: list[_Action]) -> list[_Action]:
    """Sort a step's actions by the documented priority rank (stable)."""
    return sorted(actions, key=_rank)
