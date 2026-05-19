from __future__ import annotations

from briocare.runtime.actions import (
    InviteParticipant,
    NoOp,
    PromptSource,
    SayPrompt,
)
from briocare.runtime.events import OverrideCommand
from briocare.runtime.state import Lifecycle
from briocare.scripts.schema import AdvanceWhen, TurnOrder
from tests.conftest import kinds, make_phase


def _two_phase_factory(driver_factory):
    p1 = make_phase("go_around", order=TurnOrder.ROUND_ROBIN, require_all_speak=True,
                     max_phase_seconds=600, wrapup_warning_seconds=60)
    p2 = make_phase("reflect", order=TurnOrder.OPEN, max_phase_seconds=60,
                     advance_when=AdvanceWhen.TIMER)
    return driver_factory(p1, p2)


def test_advance_phase_override_enters_next_phase(driver_factory) -> None:
    d = _two_phase_factory(driver_factory)
    d.start(at=0)
    out = d.override(OverrideCommand.ADVANCE_PHASE, at=5)
    assert "wrap_up_phase" in kinds(out) and "advance_phase" in kinds(out)
    assert d.m.state.phase is not None and d.m.state.phase.phase_id == "reflect"
    assert any(isinstance(a, SayPrompt) and a.source == PromptSource.PHASE_OPENING for a in out)


def test_goto_phase_jumps_and_unknown_phase_is_rejected(driver_factory) -> None:
    d = _two_phase_factory(driver_factory)
    d.start(at=0)
    out = d.override(OverrideCommand.GOTO_PHASE, at=3, phase_id="reflect")
    assert d.m.state.phase is not None and d.m.state.phase.phase_id == "reflect"
    assert "advance_phase" in kinds(out)
    bad = d.override(OverrideCommand.GOTO_PHASE, at=4, phase_id="nope")
    assert len(bad) == 1 and isinstance(bad[0], NoOp) and "no such phase" in bad[0].reason


def test_skip_participant_lets_phase_complete(driver_factory) -> None:
    p = make_phase("go_around", order=TurnOrder.ROUND_ROBIN, require_all_speak=True,
                    max_phase_seconds=600, wrapup_warning_seconds=60)
    d = driver_factory(p)
    d.start(at=0)
    d.speak("kid1", "a", at=1)
    d.speak("kid2", "b", at=2)
    d.speak("kid3", "c", at=3)
    # kid4 never speaks; clinician skips them -> phase can complete
    out = d.override(OverrideCommand.SKIP_PARTICIPANT, at=4, pid="kid4")
    assert "wrap_up_phase" in kinds(out)
    assert d.m.state.lifecycle == Lifecycle.ENDED


def test_mute_downgrades_spoken_actions_and_unmute_restores(driver_factory) -> None:
    p = make_phase("go_around", order=TurnOrder.ROUND_ROBIN, require_all_speak=True,
                    acknowledge=True, max_phase_seconds=600, wrapup_warning_seconds=60)
    d = driver_factory(p)
    d.start(at=0)
    d.override(OverrideCommand.MUTE_AGENT, at=1)
    muted = d.speak("kid1", "happy", at=2)
    assert all(isinstance(a, NoOp) for a in muted)
    assert any("agent_muted" in a.reason for a in muted)
    # state still advances under the hood
    assert d.m.state.phase is not None
    assert d.m.state.phase.per_participant["kid1"].spoke_count == 1
    d.override(OverrideCommand.UNMUTE_AGENT, at=3)
    restored = d.speak("kid2", "ok", at=4)
    assert any(isinstance(a, InviteParticipant) for a in restored)


def test_inject_prompt_bypasses_mute(driver_factory) -> None:
    d = _two_phase_factory(driver_factory)
    d.start(at=0)
    d.override(OverrideCommand.MUTE_AGENT, at=1)
    out = d.override(OverrideCommand.INJECT_PROMPT, at=2, text="Let's take a breath.")
    says = [a for a in out if isinstance(a, SayPrompt)]
    assert len(says) == 1
    assert says[0].source == PromptSource.INJECTED
    assert says[0].text == "Let's take a breath."


def test_pause_makes_ticks_noop_then_resume(driver_factory) -> None:
    p = make_phase("go_around", order=TurnOrder.ROUND_ROBIN, require_all_speak=True,
                    max_phase_seconds=600, wrapup_warning_seconds=60)
    d = driver_factory(p)
    d.start(at=0)
    d.override(OverrideCommand.PAUSE, at=1)
    assert d.m.state.lifecycle == Lifecycle.PAUSED
    paused = d.tick(at=100)
    assert len(paused) == 1 and isinstance(paused[0], NoOp) and paused[0].reason == "paused"
    spoke = d.speak("kid1", "hi", at=101)
    assert len(spoke) == 1 and isinstance(spoke[0], NoOp)
    d.override(OverrideCommand.RESUME, at=102)
    assert d.m.state.lifecycle == Lifecycle.IN_PHASE


def test_end_session_request_emits_closing_then_end(driver_factory) -> None:
    d = _two_phase_factory(driver_factory)
    d.start(at=0)
    out = d.end(at=10)
    assert "end_session" in kinds(out)
    assert any(isinstance(a, SayPrompt) and a.source == PromptSource.CLOSING for a in out)
    assert d.m.state.lifecycle == Lifecycle.ENDED
