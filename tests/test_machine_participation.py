from __future__ import annotations

from briocare.runtime.actions import InviteParticipant, InviteReason
from briocare.scripts.schema import AdvanceWhen, QuietStrategy, TurnOrder
from tests.conftest import make_phase


def _quiet_nudges(actions: list) -> list[InviteParticipant]:
    return [
        a
        for a in actions
        if isinstance(a, InviteParticipant) and a.reason == InviteReason.QUIET_NUDGE
    ]


def test_silent_kid_nudged_up_to_cap_then_stops(driver_factory) -> None:
    p = make_phase(
        "go_around",
        order=TurnOrder.POPCORN,  # no managed turns, so only quiet nudges drive invites
        require_all_speak=True,
        invite_quiet_after_seconds=20,
        max_invites_per_participant=2,
        quiet_strategy=QuietStrategy.DIRECT_INVITE,
        max_phase_seconds=10_000,
        advance_when=AdvanceWhen.ALL_SPOKE,
    )
    d = driver_factory(p)
    d.start(at=0)
    # everyone except kid4 speaks immediately so kid4 is the lone quiet candidate
    for pid in ["kid1", "kid2", "kid3"]:
        d.speak(pid, "hi", at=1)

    first = _quiet_nudges(d.tick(at=21))
    assert [n.participant_id for n in first] == ["kid4"]
    assert first[0].attempt == 1 and first[0].max_attempts == 2

    # next nudge only after another idle window measured from the last nudge
    assert _quiet_nudges(d.tick(at=30)) == []
    second = _quiet_nudges(d.tick(at=41))
    assert second and second[0].attempt == 2

    # cap reached -> no third nudge ever
    assert _quiet_nudges(d.tick(at=70)) == []
    assert _quiet_nudges(d.tick(at=200)) == []


def test_no_nudges_when_require_all_speak_false_and_strategy_skip(driver_factory) -> None:
    p = make_phase(
        "warmup",
        order=TurnOrder.POPCORN,
        require_all_speak=False,
        quiet_strategy=QuietStrategy.SKIP,
        max_phase_seconds=90,
        advance_when=AdvanceWhen.TIMER,
    )
    d = driver_factory(p)
    d.start(at=0)
    for t in (20, 40, 60, 89):
        assert _quiet_nudges(d.tick(at=t)) == []
    assert "advance_phase" in [a.kind for a in d.tick(at=90)]
