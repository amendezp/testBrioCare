"""Shared builders for constructing scripts in machine/policy tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from briocare.runtime.actions import FacilitatorAction
from briocare.runtime.events import (
    ClinicianOverride,
    EndSessionRequest,
    OverrideCommand,
    ParticipantRated,
    ParticipantSpoke,
    SilenceTimeout,
    StartSession,
    Tick,
)
from briocare.runtime.machine import SessionMachine
from briocare.scripts.schema import (
    AdvanceWhen,
    ExerciseScript,
    PacingRule,
    ParticipationPolicy,
    Phase,
    Prompt,
    QuietStrategy,
    TurnOrder,
    TurnPolicy,
)

FIXTURES = Path(__file__).parent / "fixtures"
LIBRARY = Path(__file__).parents[1] / "src" / "briocare" / "scripts" / "library"


def make_phase(
    phase_id: str,
    *,
    order: TurnOrder = TurnOrder.OPEN,
    require_all_speak: bool = False,
    acknowledge: bool = False,
    min_phase_seconds: int = 0,
    max_phase_seconds: int | None = None,
    wrapup_warning_seconds: int | None = None,
    advance_when: AdvanceWhen = AdvanceWhen.ALL_SPOKE_OR_TIMER,
    per_turn_hard_seconds: int = 90,
    invite_quiet_after_seconds: int = 20,
    max_invites_per_participant: int = 2,
    quiet_strategy: QuietStrategy = QuietStrategy.DIRECT_INVITE,
    transition: str | None = "Next.",
    mode: str = "conversation",
    rating_scale: int = 5,
    menu_only: bool = False,
) -> Phase:
    return Phase(
        id=phase_id,
        title=phase_id.title(),
        mode=mode,
        rating_scale=rating_scale,
        menu_only=menu_only,
        opening_prompt=Prompt(text=f"Opening {phase_id}"),
        transition_prompt=Prompt(text=transition) if transition else None,
        acknowledge_speakers=acknowledge,
        turn_policy=TurnPolicy(
            order=order,
            per_turn_seconds=min(45, per_turn_hard_seconds),
            per_turn_hard_seconds=per_turn_hard_seconds,
        ),
        participation=ParticipationPolicy(
            require_all_speak=require_all_speak,
            invite_quiet_after_seconds=invite_quiet_after_seconds,
            max_invites_per_participant=max_invites_per_participant,
            quiet_participant_strategy=quiet_strategy,
        ),
        pacing=PacingRule(
            min_phase_seconds=min_phase_seconds,
            max_phase_seconds=max_phase_seconds,
            wrapup_warning_seconds=wrapup_warning_seconds,
            advance_when=advance_when,
        ),
    )


def make_script(*phases: Phase, with_intro: bool = True, with_closing: bool = True) -> ExerciseScript:
    return ExerciseScript(
        id="test_script",
        title="Test Script",
        intro_prompt=Prompt(text="Welcome.") if with_intro else None,
        closing_prompt=Prompt(text="Goodbye.") if with_closing else None,
        phases=list(phases),
    )


def kinds(actions: list[FacilitatorAction]) -> list[str]:
    return [a.kind for a in actions]


class Driver:
    """Thin helper to feed events and read back actions in machine tests."""

    def __init__(self, script: ExerciseScript, roster: dict[str, str]) -> None:
        self.m = SessionMachine(script)
        self.roster = roster

    def start(self, at: float = 0.0) -> list[FacilitatorAction]:
        return self.m.step(StartSession(at=at, roster=self.roster))

    def speak(self, pid: str, text: str, at: float) -> list[FacilitatorAction]:
        return self.m.step(ParticipantSpoke(at=at, participant_id=pid, text=text))

    def rate(self, pid: str, value: int, at: float) -> list[FacilitatorAction]:
        return self.m.step(ParticipantRated(at=at, participant_id=pid, value=value))

    def tick(self, at: float) -> list[FacilitatorAction]:
        return self.m.step(Tick(at=at))

    def silence(self, at: float) -> list[FacilitatorAction]:
        return self.m.step(SilenceTimeout(at=at))

    def override(self, command: OverrideCommand, at: float, **args: str) -> list[FacilitatorAction]:
        return self.m.step(ClinicianOverride(at=at, command=command, args=args))

    def end(self, at: float) -> list[FacilitatorAction]:
        return self.m.step(EndSessionRequest(at=at))


@pytest.fixture
def roster() -> dict[str, str]:
    return {"kid1": "Maya", "kid2": "Leo", "kid3": "Aisha", "kid4": "Sam"}


@pytest.fixture
def driver_factory(roster: dict[str, str]):
    def _make(*phases: Phase, **script_kw: bool) -> Driver:
        return Driver(make_script(*phases, **script_kw), roster)

    return _make


@pytest.fixture
def library_script_path() -> Path:
    return LIBRARY / "feelings_checkin_circle.yaml"
