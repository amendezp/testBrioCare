from __future__ import annotations

from briocare.runtime.actions import AdvancePhase, PromptSource, SayPrompt
from briocare.runtime.state import Lifecycle
from briocare.scripts.schema import AdvanceWhen, QuietStrategy, TurnOrder
from tests.conftest import kinds, make_phase


def test_timer_phase_advances_exactly_once_at_max(driver_factory) -> None:
    p1 = make_phase("warmup", order=TurnOrder.POPCORN, max_phase_seconds=90, advance_when=AdvanceWhen.TIMER)
    p2 = make_phase("reflect", order=TurnOrder.OPEN, max_phase_seconds=60, advance_when=AdvanceWhen.TIMER)
    d = driver_factory(p1, p2)
    d.start(at=0)
    assert d.tick(at=89) == []
    crossed = d.tick(at=90)
    advances = [a for a in crossed if isinstance(a, AdvancePhase)]
    assert len(advances) == 1
    assert advances[0].from_phase == "warmup" and advances[0].to_phase == "reflect"
    assert d.m.state.phase is not None and d.m.state.phase.phase_id == "reflect"


def test_wrapup_warning_emitted_once(driver_factory) -> None:
    p = make_phase("reflect", order=TurnOrder.OPEN, max_phase_seconds=100, wrapup_warning_seconds=20,
                    advance_when=AdvanceWhen.TIMER)
    d = driver_factory(p)
    d.start(at=0)
    assert d.tick(at=79) == []
    warn = d.tick(at=80)
    assert [a for a in warn if isinstance(a, SayPrompt) and a.source == PromptSource.WRAPUP_WARNING]
    assert d.tick(at=85) == []  # not repeated
    assert d.tick(at=90) == []


def test_min_phase_seconds_blocks_early_advance_even_if_all_spoke(driver_factory) -> None:
    p = make_phase("go_around", order=TurnOrder.ROUND_ROBIN, require_all_speak=True,
                    min_phase_seconds=30, max_phase_seconds=600, wrapup_warning_seconds=60,
                    advance_when=AdvanceWhen.ALL_SPOKE_OR_TIMER)
    d = driver_factory(p)
    d.start(at=0)
    for i, pid in enumerate(["kid1", "kid2", "kid3", "kid4"], start=1):
        out = d.speak(pid, "hi", at=i)
    # everyone spoke by t=4 but min_phase_seconds=30 -> not complete yet
    assert "wrap_up_phase" not in kinds(out)
    assert d.m.state.lifecycle == Lifecycle.IN_PHASE
    # a deadline exists at the min boundary
    assert d.m.next_deadline() == 30
    closed = d.tick(at=30)
    assert "wrap_up_phase" in kinds(closed)


def test_next_deadline_tracks_state(driver_factory) -> None:
    p = make_phase("go_around", order=TurnOrder.ROUND_ROBIN, require_all_speak=True,
                    per_turn_hard_seconds=40, max_phase_seconds=300, wrapup_warning_seconds=30,
                    quiet_strategy=QuietStrategy.SKIP)
    d = driver_factory(p)
    assert d.m.next_deadline() is None  # not started
    d.start(at=0)
    # earliest of: turn hard cap (0+40), wrapup (300-30=270), max (300), min (0, filtered)
    assert d.m.next_deadline() == 40
    d.speak("kid1", "hi", at=10)  # turn -> kid2, turn_started_at=10
    assert d.m.next_deadline() == 50
