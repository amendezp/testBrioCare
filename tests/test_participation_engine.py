"""Feature 1 — need-weighted nudges, echo cues, and the spontaneity metric."""

from __future__ import annotations

from briocare.runtime.actions import InviteParticipant, InviteReason, SuggestEcho
from briocare.scripts.schema import AdvanceWhen, TurnOrder
from tests.conftest import make_phase


def _quiet_nudges(actions: list) -> list[InviteParticipant]:
    return [a for a in actions if isinstance(a, InviteParticipant) and a.reason == InviteReason.QUIET_NUDGE]


def _echoes(actions: list) -> list[SuggestEcho]:
    return [a for a in actions if isinstance(a, SuggestEcho)]


def test_quiet_nudge_targets_most_inhibited_not_roster_order(driver_factory) -> None:
    """kid1 talks a lot in phase A; in phase B (where nobody has spoken yet) the need-weighted
    nudge must skip kid1 and reach the least-heard child, not just the first in roster order."""
    phase_a = make_phase("warmup", order=TurnOrder.POPCORN, max_phase_seconds=5, advance_when=AdvanceWhen.TIMER)
    phase_b = make_phase(
        "go_around",
        order=TurnOrder.POPCORN,
        require_all_speak=True,
        invite_quiet_after_seconds=20,
        max_phase_seconds=10_000,
        advance_when=AdvanceWhen.TIMER,
    )
    d = driver_factory(phase_a, phase_b)
    d.start(at=0)
    d.speak("kid1", "lots", at=1)
    d.speak("kid1", "more", at=2)
    d.tick(at=5)  # phase A timer -> enter phase B (fresh: nobody has spoken)

    nudges = _quiet_nudges(d.tick(at=25))  # idle window from phase-B entry (t=5)
    assert nudges, "a quiet nudge should fire"
    # roster order would pick kid1 first; need-weighting deprioritises the most-talkative child.
    assert nudges[0].participant_id == "kid2"
    assert d.m.state.contributions["kid1"] == 2


def test_echo_cue_fires_once_for_a_nudged_child_only(driver_factory) -> None:
    p = make_phase(
        "go_around",
        order=TurnOrder.POPCORN,
        require_all_speak=True,
        invite_quiet_after_seconds=20,
        max_phase_seconds=10_000,
        advance_when=AdvanceWhen.TIMER,
    )
    d = driver_factory(p)
    d.start(at=0)
    assert _quiet_nudges(d.tick(at=21))[0].participant_id == "kid1"  # kid1 nudged first

    first = _echoes(d.speak("kid1", "i feel a little shy", at=22))
    assert len(first) == 1 and first[0].participant_id == "kid1" and "shy" in first[0].text
    assert _echoes(d.speak("kid1", "and ok now", at=23)) == []  # only once per phase
    assert _echoes(d.speak("kid2", "i'm great", at=24)) == []  # kid2 was never nudged


def test_spontaneous_counts_out_of_turn_not_managed_turns(driver_factory) -> None:
    p = make_phase(
        "go_around",
        order=TurnOrder.ROUND_ROBIN,
        require_all_speak=True,
        max_phase_seconds=10_000,
        advance_when=AdvanceWhen.TIMER,
    )
    d = driver_factory(p)
    d.start(at=0)  # kid1 gets the first managed turn
    d.speak("kid1", "on my turn", at=1)  # was current turn -> NOT spontaneous
    d.speak("kid3", "jumping in", at=2)  # not their managed turn -> spontaneous
    assert d.m.state.spontaneous.get("kid1", 0) == 0
    assert d.m.state.spontaneous.get("kid3", 0) == 1
    assert d.m.state.contributions["kid1"] == 1 and d.m.state.contributions["kid3"] == 1
