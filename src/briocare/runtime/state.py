"""Mutable session state.

Fully serializable (pydantic) so a session can be snapshotted / replayed.
Unlike events and actions these models are *not* frozen — the machine mutates
them in place.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Lifecycle(str, Enum):
    NOT_STARTED = "not_started"
    INTRO = "intro"
    IN_PHASE = "in_phase"
    BETWEEN_PHASES = "between_phases"
    CLOSING = "closing"
    ENDED = "ended"
    PAUSED = "paused"


class ParticipantPhaseState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spoke_count: int = 0
    passed: bool = False
    invites_received: int = 0
    last_spoke_at: float | None = None
    skipped: bool = False


class PhaseRuntimeState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phase_id: str
    entered_at: float
    current_turn: str | None = None
    turn_started_at: float | None = None
    order_cursor: int = 0
    per_participant: dict[str, ParticipantPhaseState] = Field(default_factory=dict)
    wrapup_warned: bool = False
    last_nudge_at: float | None = None

    def ps(self, pid: str) -> ParticipantPhaseState:
        return self.per_participant.setdefault(pid, ParticipantPhaseState())


class SessionState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    script_id: str
    roster: dict[str, str] = Field(default_factory=dict)
    lifecycle: Lifecycle = Lifecycle.NOT_STARTED
    phase_index: int = -1
    phase: PhaseRuntimeState | None = None
    agent_muted: bool = False
    paused: bool = False
    last_any_speech_at: float | None = None
    history: list[dict[str, Any]] = Field(default_factory=list)

    def record(self, at: float, type_: str, model: BaseModel) -> None:
        payload = model.model_dump(mode="json")
        self.history.append(
            {"at": at, "type": type_, "kind": payload.get("kind", ""), "payload": payload}
        )
