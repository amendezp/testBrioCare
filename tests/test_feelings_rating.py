"""Feature 2 (engine) — feelings-thermometer rating phases."""

from __future__ import annotations

from briocare.runtime.actions import RequestRating
from briocare.scripts.schema import AdvanceWhen, TurnOrder
from tests.conftest import kinds, make_phase


def _rating_script(driver_factory):
    checkin = make_phase(
        "checkin", mode="rating", rating_scale=5, max_phase_seconds=60, advance_when=AdvanceWhen.TIMER
    )
    talk = make_phase("talk", order=TurnOrder.ROUND_ROBIN, max_phase_seconds=10_000, advance_when=AdvanceWhen.TIMER)
    return driver_factory(checkin, talk)


def test_rating_phase_requests_rating_and_assigns_no_turn(driver_factory) -> None:
    d = _rating_script(driver_factory)
    acts = d.start(at=0)
    requests = [a for a in acts if isinstance(a, RequestRating)]
    assert len(requests) == 1 and requests[0].scale == 5 and requests[0].prompt_text
    assert not any(a.kind == "invite_participant" for a in acts)  # no managed speaking turn
    assert d.m.state.phase is not None and d.m.state.phase.current_turn is None


def test_all_rated_advances_early_and_stores_ratings(driver_factory) -> None:
    d = _rating_script(driver_factory)
    d.start(at=0)
    d.rate("kid1", 4, at=1)
    d.rate("kid2", 3, at=1)
    d.rate("kid3", 5, at=1)
    acts = d.rate("kid4", 2, at=2)  # last one -> phase completes early
    assert "advance_phase" in kinds(acts)
    assert d.m.state.ratings["checkin"] == {"kid1": 4, "kid2": 3, "kid3": 5, "kid4": 2}
    assert d.m.state.phase_index == 1  # advanced into the talk phase


def test_timer_backstops_a_non_responder(driver_factory) -> None:
    d = _rating_script(driver_factory)
    d.start(at=0)
    d.rate("kid1", 4, at=1)  # only one child taps
    assert "advance_phase" not in kinds(d.tick(at=30))  # not everyone rated yet
    assert "advance_phase" in kinds(d.tick(at=60))  # timer backstop advances the phase


def test_rating_in_a_conversation_phase_is_ignored(driver_factory) -> None:
    d = _rating_script(driver_factory)
    d.start(at=0)
    for pid in ("kid1", "kid2", "kid3", "kid4"):
        d.rate(pid, 3, at=1)  # advances into the conversation phase
    acts = d.rate("kid1", 5, at=2)
    assert kinds(acts) == ["no_op"]  # ratings only count during a rating phase
