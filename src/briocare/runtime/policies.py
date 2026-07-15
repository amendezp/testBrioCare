"""Pure decision functions used by :class:`SessionMachine`.

These never mutate state — they read a :class:`PhaseRuntimeState` (plus the
phase config / clock) and return a decision.
"""

from __future__ import annotations

from briocare.runtime.state import PhaseRuntimeState
from briocare.scripts.schema import AdvanceWhen, Phase, QuietStrategy, TurnOrder


def _done(state: PhaseRuntimeState, pid: str) -> bool:
    ps = state.per_participant.get(pid)
    if ps is None:
        return False
    return ps.spoke_count > 0 or ps.passed or ps.skipped


def all_participants_done(state: PhaseRuntimeState, roster: list[str]) -> bool:
    return all(_done(state, pid) for pid in roster)


def all_participants_rated(state: PhaseRuntimeState, roster: list[str]) -> bool:
    """True once every rostered child has submitted a rating this (rating) phase."""
    for pid in roster:
        ps = state.per_participant.get(pid)
        if ps is None or ps.rating is None:
            return False
    return True


def rank_by_need(
    cands: list[str],
    contributions: dict[str, int],
    state: PhaseRuntimeState,
    roster: list[str],
) -> list[str]:
    """Order quiet-nudge candidates most-inhibited first.

    Need = fewest cumulative contributions (across the whole session), then fewest
    invites already received, then roster order — fully deterministic. The eligibility
    filter (:func:`quiet_candidates`) is unchanged; this only re-prioritises it, so the
    therapist is steered toward the child who most needs a low-pressure invitation.
    """

    def key(pid: str) -> tuple[int, int, int]:
        ps = state.per_participant.get(pid)
        invites = ps.invites_received if ps else 0
        idx = roster.index(pid) if pid in roster else len(roster)
        return (contributions.get(pid, 0), invites, idx)

    return sorted(cands, key=key)


def next_turn(
    order: TurnOrder,
    roster: list[str],
    state: PhaseRuntimeState,
    *,
    start: int,
    one_turn: bool = True,
) -> str | None:
    """Next participant id to take a managed turn, or ``None``.

    ``round_robin`` scans ``roster[start:]`` (no wrap-around — each participant
    gets one turn).  ``facilitator_pick`` ignores ``start`` and chooses the
    least-heard participant (skipping spoke ones only when ``one_turn``).
    ``popcorn`` / ``open`` have no managed turns.
    """
    if order == TurnOrder.ROUND_ROBIN:
        for pid in roster[start:]:
            if not _done(state, pid):
                return pid
        return None
    if order == TurnOrder.FACILITATOR_PICK:

        def eligible_pid(pid: str) -> bool:
            ps = state.per_participant.get(pid)
            if ps is None:
                return True
            if ps.passed or ps.skipped:
                return False
            return not (one_turn and ps.spoke_count > 0)

        eligible = [pid for pid in roster if eligible_pid(pid)]
        if not eligible:
            return None

        def key(pid: str) -> tuple[int, float]:
            ps = state.per_participant.get(pid)
            spoke = ps.spoke_count if ps else 0
            last = ps.last_spoke_at if ps and ps.last_spoke_at is not None else float("-inf")
            return (spoke, last)

        return min(eligible, key=key)
    return None


def phase_complete(phase: Phase, state: PhaseRuntimeState, roster: list[str], now: float) -> bool:
    if phase.pacing.advance_when == AdvanceWhen.MANUAL:
        return False  # only the clinician's override completes a manual phase
    elapsed = now - state.entered_at
    if elapsed < phase.pacing.min_phase_seconds:
        return False
    all_done = all_participants_done(state, roster)
    timer_done = (
        phase.pacing.max_phase_seconds is not None and elapsed >= phase.pacing.max_phase_seconds
    )
    if phase.pacing.advance_when == AdvanceWhen.ALL_SPOKE:
        return all_done
    if phase.pacing.advance_when == AdvanceWhen.TIMER:
        return timer_done
    return all_done or timer_done


def quiet_candidates(
    phase: Phase,
    state: PhaseRuntimeState,
    roster: list[str],
    now: float,
    idle_since: float,
) -> list[str]:
    """Participants eligible for a quiet-nudge invite right now.

    ``idle_since`` is the later of phase entry, last speech, and last nudge —
    so nudges are spaced at least ``invite_quiet_after_seconds`` apart.
    """
    pol = phase.participation
    if not pol.require_all_speak or pol.quiet_participant_strategy == QuietStrategy.SKIP:
        return []
    if now - idle_since < pol.invite_quiet_after_seconds:
        return []
    out: list[str] = []
    for pid in roster:
        ps = state.per_participant.get(pid)
        if ps is not None and (ps.spoke_count > 0 or ps.passed or ps.skipped):
            continue
        invites = ps.invites_received if ps else 0
        if invites < pol.max_invites_per_participant:
            out.append(pid)
    return out


def should_warn_wrapup(phase: Phase, state: PhaseRuntimeState, now: float) -> bool:
    if state.wrapup_warned:
        return False
    pacing = phase.pacing
    if pacing.max_phase_seconds is None or pacing.wrapup_warning_seconds is None:
        return False
    elapsed = now - state.entered_at
    if elapsed >= pacing.max_phase_seconds:
        return False  # past the cap — the phase is advancing, not warning
    return elapsed >= pacing.max_phase_seconds - pacing.wrapup_warning_seconds
