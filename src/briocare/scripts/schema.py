"""Declarative exercise-script schema.

A script is an ordered list of phases.  Participants are *not* part of a script;
a roster is supplied at session start.  All models are strict (unknown keys are
rejected) so authoring mistakes surface immediately.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION: Literal[1] = 1


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TurnOrder(str, Enum):
    ROUND_ROBIN = "round_robin"
    POPCORN = "popcorn"
    FACILITATOR_PICK = "facilitator_pick"
    OPEN = "open"


class QuietStrategy(str, Enum):
    DIRECT_INVITE = "direct_invite"
    GENTLE_OPEN_INVITE = "gentle_open_invite"
    SKIP = "skip"


class AdvanceWhen(str, Enum):
    ALL_SPOKE = "all_spoke"
    TIMER = "timer"
    ALL_SPOKE_OR_TIMER = "all_spoke_or_timer"
    MANUAL = "manual"  # only the clinician advances (Next activity) — never auto


class Prompt(_Strict):
    text: str
    variants: list[str] = Field(default_factory=list)
    addressed_to: Literal["group", "current_turn"] = "group"

    @model_validator(mode="before")
    @classmethod
    def _coerce_str(cls, data: Any) -> Any:
        if isinstance(data, str):
            return {"text": data}
        return data

    @field_validator("text")
    @classmethod
    def _strip_text(cls, v: str) -> str:
        return " ".join(v.split())


class TurnPolicy(_Strict):
    order: TurnOrder = TurnOrder.OPEN
    per_turn_seconds: int = 45
    per_turn_hard_seconds: int = 90
    allow_pass: bool = True
    one_turn_per_participant: bool = True

    @model_validator(mode="after")
    def _check(self) -> TurnPolicy:
        if self.per_turn_hard_seconds < self.per_turn_seconds:
            raise ValueError("per_turn_hard_seconds must be >= per_turn_seconds")
        return self


class ParticipationPolicy(_Strict):
    require_all_speak: bool = False
    invite_quiet_after_seconds: int = 30
    max_invites_per_participant: int = 2
    quiet_participant_strategy: QuietStrategy = QuietStrategy.DIRECT_INVITE
    honor_pass: bool = True


class PacingRule(_Strict):
    min_phase_seconds: int = 0
    max_phase_seconds: int | None = None
    wrapup_warning_seconds: int | None = None
    advance_when: AdvanceWhen = AdvanceWhen.ALL_SPOKE_OR_TIMER

    @model_validator(mode="after")
    def _check(self) -> PacingRule:
        if self.advance_when == AdvanceWhen.TIMER and self.max_phase_seconds is None:
            raise ValueError("advance_when='timer' requires max_phase_seconds")
        if self.wrapup_warning_seconds is not None:
            if self.max_phase_seconds is None:
                raise ValueError("wrapup_warning_seconds requires max_phase_seconds")
            if self.wrapup_warning_seconds >= self.max_phase_seconds:
                raise ValueError("wrapup_warning_seconds must be < max_phase_seconds")
        if self.max_phase_seconds is not None and self.min_phase_seconds > self.max_phase_seconds:
            raise ValueError("min_phase_seconds must be <= max_phase_seconds")
        return self


class Phase(_Strict):
    id: str
    title: str
    # "conversation" (default): turn-taking / open discussion.
    # "rating": each child taps a feelings-thermometer value (no managed speaking turns).
    mode: Literal["conversation", "rating"] = "conversation"
    rating_scale: int = 5  # number of points on the thermometer when mode == "rating"
    # menu_only phases are an on-demand activity library: skipped by linear auto-advance,
    # launched by the clinician via goto_phase, and they return to a ready state when done.
    menu_only: bool = False
    opening_prompt: Prompt
    transition_prompt: Prompt | None = None
    turn_policy: TurnPolicy = Field(default_factory=TurnPolicy)
    participation: ParticipationPolicy = Field(default_factory=ParticipationPolicy)
    pacing: PacingRule = Field(default_factory=PacingRule)
    acknowledge_speakers: bool = False
    facilitator_notes: str | None = None

    @model_validator(mode="after")
    def _check_rating(self) -> Phase:
        if self.mode == "rating" and self.rating_scale < 2:
            raise ValueError("rating_scale must be >= 2 for a rating phase")
        return self


class ExerciseScript(_Strict):
    schema_version: Literal[1] = SCHEMA_VERSION
    id: str
    title: str
    description: str | None = None
    age_range: tuple[int, int] | None = None
    recommended_group_size: tuple[int, int] | None = None
    intro_prompt: Prompt | None = None
    closing_prompt: Prompt | None = None
    phases: list[Phase] = Field(min_length=1)

    @field_validator("phases")
    @classmethod
    def _unique_ids(cls, phases: list[Phase]) -> list[Phase]:
        ids = [p.id for p in phases]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate phase ids: {sorted(dupes)}")
        return phases

    def phase_by_id(self, phase_id: str) -> Phase:
        for p in self.phases:
            if p.id == phase_id:
                return p
        raise KeyError(phase_id)

    def phase_index(self, phase_id: str) -> int:
        for i, p in enumerate(self.phases):
            if p.id == phase_id:
                return i
        raise KeyError(phase_id)
