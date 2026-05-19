from __future__ import annotations

from briocare.runtime.actions import (
    AdvancePhase,
    InviteParticipant,
    InviteReason,
    PromptSource,
    SayPrompt,
)
from briocare.runtime.state import Lifecycle
from briocare.scripts.schema import TurnOrder
from tests.conftest import kinds, make_phase


def _go_around() -> object:
    return make_phase(
        "go_around",
        order=TurnOrder.ROUND_ROBIN,
        require_all_speak=True,
        acknowledge=True,
        min_phase_seconds=0,
        max_phase_seconds=600,
        wrapup_warning_seconds=60,
    )


def test_start_emits_intro_opening_and_first_invite(driver_factory) -> None:
    d = driver_factory(_go_around())
    actions = d.start(at=0)
    assert kinds(actions) == ["say_prompt", "say_prompt", "invite_participant"]
    assert actions[0].source == PromptSource.INTRO
    assert actions[1].source == PromptSource.PHASE_OPENING
    assert isinstance(actions[2], InviteParticipant)
    assert actions[2].participant_id == "kid1"
    assert actions[2].reason == InviteReason.ROUND_ROBIN_TURN
    assert d.m.state.lifecycle == Lifecycle.IN_PHASE


def test_each_speaker_is_acked_and_next_is_invited(driver_factory) -> None:
    d = driver_factory(_go_around())
    d.start(at=0)
    a1 = d.speak("kid1", "happy", at=5)
    assert kinds(a1) == ["acknowledge_speaker", "invite_participant"]
    assert a1[1].participant_id == "kid2"
    a2 = d.speak("kid2", "tired", at=10)
    assert a2[1].participant_id == "kid3"


def test_round_completes_after_last_speaker(driver_factory) -> None:
    d = driver_factory(_go_around())
    d.start(at=0)
    d.speak("kid1", "a", at=1)
    d.speak("kid2", "b", at=2)
    d.speak("kid3", "c", at=3)
    final = d.speak("kid4", "d", at=4)
    ks = kinds(final)
    assert "wrap_up_phase" in ks
    assert "advance_phase" in ks
    advance = next(a for a in final if isinstance(a, AdvancePhase))
    assert advance.from_phase == "go_around"
    # only one phase -> session closes
    assert d.m.state.lifecycle == Lifecycle.ENDED
    assert any(isinstance(a, SayPrompt) and a.source == PromptSource.CLOSING for a in final)


def test_pass_is_not_reinvited_and_does_not_block_completion(driver_factory) -> None:
    d = driver_factory(_go_around())
    d.start(at=0)
    d.speak("kid1", "ok", at=1)
    passed = d.speak("kid2", "pass", at=2)
    # turn moves to kid3, kid2 not invited again
    assert passed[-1].participant_id == "kid3"
    assert d.m.state.phase is not None
    assert d.m.state.phase.per_participant["kid2"].passed is True
    d.speak("kid3", "ok", at=3)
    final = d.speak("kid4", "ok", at=4)
    assert "wrap_up_phase" in kinds(final)


def test_turn_hard_cap_moves_turn_on_with_no_speech(driver_factory) -> None:
    phase = make_phase(
        "go_around",
        order=TurnOrder.ROUND_ROBIN,
        require_all_speak=True,
        per_turn_hard_seconds=30,
        max_phase_seconds=600,
    )
    d = driver_factory(phase)
    d.start(at=0)
    assert d.m.state.phase is not None
    assert d.m.state.phase.current_turn == "kid1"
    moved = d.tick(at=30)
    invite = next(a for a in moved if isinstance(a, InviteParticipant))
    assert invite.participant_id == "kid2"
    assert d.m.state.phase.current_turn == "kid2"


def test_non_turn_phase_has_no_invites(driver_factory) -> None:
    phase = make_phase("warmup", order=TurnOrder.POPCORN, max_phase_seconds=90)
    d = driver_factory(phase)
    start = d.start(at=0)
    assert kinds(start) == ["say_prompt", "say_prompt"]
    spoke = d.speak("kid2", "excited!", at=5)
    assert all(not isinstance(a, InviteParticipant) for a in spoke)
