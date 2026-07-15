"""On-demand activity library: menu_only phases are launched by the clinician via
goto_phase, skipped by linear auto-advance, and return to a ready state when done."""

from __future__ import annotations

from briocare.runtime.events import OverrideCommand
from briocare.runtime.state import Lifecycle
from briocare.scripts.schema import AdvanceWhen, TurnOrder
from tests.conftest import kinds, make_phase


def _menu(pid: str):
    return make_phase(pid, menu_only=True, order=TurnOrder.POPCORN, max_phase_seconds=5, advance_when=AdvanceWhen.TIMER)


def test_linear_autoadvance_skips_menu_only(driver_factory) -> None:
    a = make_phase("a", order=TurnOrder.POPCORN, max_phase_seconds=5, advance_when=AdvanceWhen.TIMER)
    lib = _menu("lib")
    b = make_phase("b", order=TurnOrder.POPCORN, max_phase_seconds=5, advance_when=AdvanceWhen.TIMER)
    d = driver_factory(a, lib, b)
    d.start(at=0)  # enters "a"
    d.tick(at=6)  # "a" completes -> linear advance skips "lib", lands on "b"
    assert d.m.state.phase is not None and d.m.state.phase.phase_id == "b"


def test_goto_launches_menu_activity_then_returns_to_ready(driver_factory) -> None:
    linear = make_phase("warmup", order=TurnOrder.POPCORN, max_phase_seconds=999, advance_when=AdvanceWhen.TIMER)
    act = _menu("act_x")
    d = driver_factory(linear, act)
    d.start(at=0)  # enters warmup
    d.override(OverrideCommand.GOTO_PHASE, at=1, phase_id="act_x")
    assert d.m.state.phase is not None and d.m.state.phase.phase_id == "act_x"

    out = d.tick(at=7)  # the activity hits its timer
    assert "wrap_up_phase" in kinds(out)
    assert d.m.state.lifecycle == Lifecycle.BETWEEN_PHASES  # ready/rest…
    assert d.m.state.phase is None  # …NOT auto-chained into another phase
    assert d.m.state.lifecycle != Lifecycle.ENDED  # and the session is not over


def test_can_launch_another_activity_from_ready(driver_factory) -> None:
    linear = make_phase("warmup", order=TurnOrder.POPCORN, max_phase_seconds=999, advance_when=AdvanceWhen.TIMER)
    d = driver_factory(linear, _menu("a1"), _menu("a2"))
    d.start(at=0)
    d.override(OverrideCommand.GOTO_PHASE, at=1, phase_id="a1")
    d.tick(at=7)  # a1 done -> ready
    d.override(OverrideCommand.GOTO_PHASE, at=8, phase_id="a2")  # launch the next from rest
    assert d.m.state.phase is not None and d.m.state.phase.phase_id == "a2"
    assert d.m.state.lifecycle == Lifecycle.IN_PHASE


def test_manual_phase_only_advances_by_therapist(driver_factory) -> None:
    """advance_when: manual — everyone speaking and any amount of time must NOT complete
    the phase; only the clinician's advance does."""
    p = make_phase(
        "share", order=TurnOrder.ROUND_ROBIN, require_all_speak=True, advance_when=AdvanceWhen.MANUAL
    )
    tail = make_phase("closing_phase", order=TurnOrder.POPCORN, max_phase_seconds=999, advance_when=AdvanceWhen.TIMER)
    d = driver_factory(p, tail)
    d.start(at=0)
    for i, pid in enumerate(["kid1", "kid2", "kid3", "kid4"]):
        d.speak(pid, "sharing", at=1 + i)
    assert d.m.state.phase is not None and d.m.state.phase.phase_id == "share"  # all spoke, still here
    d.tick(at=10_000)
    assert d.m.state.phase is not None and d.m.state.phase.phase_id == "share"  # time doesn't advance it
    out = d.override(OverrideCommand.ADVANCE_PHASE, at=10_001)
    assert "advance_phase" in kinds(out)
    assert d.m.state.phase is not None and d.m.state.phase.phase_id == "closing_phase"


def test_advance_from_ready_resumes_linear_script(driver_factory) -> None:
    """Jumping into an activity mid-script no longer abandons it: when the activity ends,
    the clinician's next advance resumes the linear script where it left off."""
    a = make_phase("share", order=TurnOrder.POPCORN, advance_when=AdvanceWhen.MANUAL)
    act = _menu("act_x")
    b = make_phase("checkout_phase", order=TurnOrder.POPCORN, max_phase_seconds=999, advance_when=AdvanceWhen.TIMER)
    d = driver_factory(a, act, b)
    d.start(at=0)  # in "share"
    d.override(OverrideCommand.GOTO_PHASE, at=1, phase_id="act_x")  # remembers resume point
    assert d.m.state.pending_linear_index is not None
    d.tick(at=7)  # activity timer -> ready state
    assert d.m.state.lifecycle == Lifecycle.BETWEEN_PHASES and d.m.state.phase is None
    d.override(OverrideCommand.ADVANCE_PHASE, at=8)  # resume the script
    assert d.m.state.phase is not None and d.m.state.phase.phase_id == "checkout_phase"
    assert d.m.state.pending_linear_index is None
