"""Input events fed to :class:`~briocare.runtime.machine.SessionMachine`.

Every variant is a frozen pydantic model with a ``kind`` discriminator tag and
an ``at`` timestamp (logical seconds since ``StartSession``).
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class OverrideCommand(str, Enum):
    ADVANCE_PHASE = "advance_phase"
    GOTO_PHASE = "goto_phase"
    SET_TURN = "set_turn"
    SKIP_PARTICIPANT = "skip_participant"
    PAUSE = "pause"
    RESUME = "resume"
    MUTE_AGENT = "mute_agent"
    UNMUTE_AGENT = "unmute_agent"
    INJECT_PROMPT = "inject_prompt"
    END_SESSION = "end_session"


class _Event(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    at: float


class StartSession(_Event):
    kind: Literal["start_session"] = "start_session"
    roster: dict[str, str]
    """Maps participant id -> display name; iteration order is the turn order."""


class ParticipantSpoke(_Event):
    kind: Literal["participant_spoke"] = "participant_spoke"
    participant_id: str
    text: str
    duration: float | None = None


class SilenceTimeout(_Event):
    kind: Literal["silence_timeout"] = "silence_timeout"


class Tick(_Event):
    kind: Literal["tick"] = "tick"


class ClinicianOverride(_Event):
    kind: Literal["clinician_override"] = "clinician_override"
    command: OverrideCommand
    args: dict[str, str] = Field(default_factory=dict)


class EndSessionRequest(_Event):
    kind: Literal["end_session_request"] = "end_session_request"


InputEvent = Annotated[
    StartSession | ParticipantSpoke | SilenceTimeout | Tick | ClinicianOverride | EndSessionRequest,
    Field(discriminator="kind"),
]
