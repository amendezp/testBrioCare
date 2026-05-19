from __future__ import annotations

from briocare.runtime.policies import (
    next_turn,
    phase_complete,
    quiet_candidates,
    should_warn_wrapup,
)
from briocare.runtime.state import ParticipantPhaseState, PhaseRuntimeState
from briocare.scripts.schema import AdvanceWhen, TurnOrder
from tests.conftest import make_phase

ROSTER = ["kid1", "kid2", "kid3", "kid4"]


def _state(entered_at: float = 0.0, **pps: ParticipantPhaseState) -> PhaseRuntimeState:
    return PhaseRuntimeState(phase_id="p", entered_at=entered_at, per_participant=dict(pps))


def test_next_turn_round_robin_skips_done_and_returns_none_when_finished() -> None:
    st = _state(
        kid1=ParticipantPhaseState(spoke_count=1),
        kid2=ParticipantPhaseState(passed=True),
    )
    assert next_turn(TurnOrder.ROUND_ROBIN, ROSTER, st, start=0) == "kid3"
    st.per_participant["kid3"] = ParticipantPhaseState(skipped=True)
    st.per_participant["kid4"] = ParticipantPhaseState(spoke_count=1)
    assert next_turn(TurnOrder.ROUND_ROBIN, ROSTER, st, start=0) is None


def test_next_turn_facilitator_pick_lowest_then_oldest() -> None:
    st = _state(
        kid1=ParticipantPhaseState(spoke_count=2, last_spoke_at=10),
        kid2=ParticipantPhaseState(spoke_count=1, last_spoke_at=30),
        kid3=ParticipantPhaseState(spoke_count=1, last_spoke_at=5),
        kid4=ParticipantPhaseState(spoke_count=1, last_spoke_at=20),
    )
    # multiple turns allowed; lowest spoke_count is 1, tie-break oldest last_spoke_at
    assert next_turn(TurnOrder.FACILITATOR_PICK, ROSTER, st, start=0, one_turn=False) == "kid3"


def test_next_turn_open_and_popcorn_have_no_managed_turn() -> None:
    st = _state()
    assert next_turn(TurnOrder.OPEN, ROSTER, st, start=0) is None
    assert next_turn(TurnOrder.POPCORN, ROSTER, st, start=0) is None


def test_phase_complete_respects_min_seconds() -> None:
    phase = make_phase("p", min_phase_seconds=30, advance_when=AdvanceWhen.ALL_SPOKE)
    st = _state(**{k: ParticipantPhaseState(spoke_count=1) for k in ROSTER})
    assert phase_complete(phase, st, ROSTER, now=20) is False
    assert phase_complete(phase, st, ROSTER, now=30) is True


def test_phase_complete_all_spoke() -> None:
    phase = make_phase("p", advance_when=AdvanceWhen.ALL_SPOKE)
    st = _state(
        kid1=ParticipantPhaseState(spoke_count=1),
        kid2=ParticipantPhaseState(passed=True),
        kid3=ParticipantPhaseState(skipped=True),
    )
    assert phase_complete(phase, st, ROSTER, now=5) is False  # kid4 not done
    st.per_participant["kid4"] = ParticipantPhaseState(spoke_count=1)
    assert phase_complete(phase, st, ROSTER, now=5) is True


def test_phase_complete_on_timer_even_if_not_all_spoke() -> None:
    phase = make_phase("p", max_phase_seconds=60, advance_when=AdvanceWhen.TIMER)
    st = _state()
    assert phase_complete(phase, st, ROSTER, now=59) is False
    assert phase_complete(phase, st, ROSTER, now=60) is True


def test_quiet_candidates_filters_cap_pass_and_idle() -> None:
    phase = make_phase("p", require_all_speak=True, invite_quiet_after_seconds=20,
                        max_invites_per_participant=2)
    st = _state(
        kid1=ParticipantPhaseState(spoke_count=1),
        kid2=ParticipantPhaseState(passed=True),
        kid3=ParticipantPhaseState(invites_received=2),
    )
    # not idle long enough yet
    assert quiet_candidates(phase, st, ROSTER, now=10, idle_since=0) == []
    # idle threshold crossed; kid1 spoke, kid2 passed, kid3 at cap -> only kid4
    assert quiet_candidates(phase, st, ROSTER, now=25, idle_since=0) == ["kid4"]


def test_quiet_candidates_skip_strategy_returns_nothing() -> None:
    from briocare.scripts.schema import QuietStrategy

    phase = make_phase("p", quiet_strategy=QuietStrategy.SKIP)
    st = _state()
    assert quiet_candidates(phase, st, ROSTER, now=999, idle_since=0) == []


def test_should_warn_wrapup_once_at_threshold() -> None:
    phase = make_phase("p", max_phase_seconds=100, wrapup_warning_seconds=20)
    st = _state()
    assert should_warn_wrapup(phase, st, now=79) is False
    assert should_warn_wrapup(phase, st, now=80) is True
    st.wrapup_warned = True
    assert should_warn_wrapup(phase, st, now=85) is False


def test_should_warn_wrapup_suppressed_past_max() -> None:
    phase = make_phase("p", max_phase_seconds=100, wrapup_warning_seconds=20)
    st = _state()
    assert should_warn_wrapup(phase, st, now=100) is False
