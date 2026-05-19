"""Output side of the loop.

``FacilitatorSink`` is one of the three Protocols the future voice app reuses
verbatim (alongside :class:`~briocare.io.participant_source.ParticipantSource`
and :class:`~briocare.runtime.clock.Clock`).  Today's concrete sinks render to
a console or JSONL; the future ``VoiceSink`` will route spoken actions through
an LLM phraser and TTS while keeping ``AdvancePhase`` / ``WrapUpPhase`` /
``NoOp`` as silent control signals.
"""

from __future__ import annotations

import json
import sys
from typing import Protocol, TextIO, runtime_checkable

from rich.console import Console

from briocare.runtime.actions import (
    AcknowledgeSpeaker,
    AdvancePhase,
    EndSession,
    FacilitatorAction,
    InviteParticipant,
    InviteReason,
    NoOp,
    SayPrompt,
    WrapUpPhase,
)


@runtime_checkable
class FacilitatorSink(Protocol):
    """Consumes the list of actions returned by one ``SessionMachine.step``."""

    def emit(self, actions: list[FacilitatorAction]) -> None: ...

    def close(self) -> None: ...


def _t(at: float) -> str:
    return f"t={at:g}"


def render_action(action: FacilitatorAction, roster: dict[str, str]) -> str:
    """One-line ``[t=<sec>] <VERB> <args>`` rendering of an action."""

    def name(pid: str) -> str:
        return roster.get(pid, pid)

    if isinstance(action, SayPrompt):
        loc = f"{action.source.value}:{action.phase_id}" if action.phase_id else action.source.value
        return f'[{_t(action.at)}] SAY ({loc}): "{action.text}"'
    if isinstance(action, InviteParticipant):
        if action.reason == InviteReason.QUIET_NUDGE and action.attempt is not None:
            who = name(action.participant_id)
            return f"[{_t(action.at)}] QUIET-NUDGE {who} (attempt {action.attempt}/{action.max_attempts})"
        return f"[{_t(action.at)}] INVITE {name(action.participant_id)} ({action.reason.value})"
    if isinstance(action, AcknowledgeSpeaker):
        extra = f': "{action.text}"' if action.text else ""
        return f"[{_t(action.at)}] ACK {name(action.participant_id)}{extra}"
    if isinstance(action, WrapUpPhase):
        return f"[{_t(action.at)}] WRAPUP {action.phase_id}"
    if isinstance(action, AdvancePhase):
        return f"[{_t(action.at)}] ADVANCE {action.from_phase} -> {action.to_phase or '(end)'}"
    if isinstance(action, EndSession):
        return f"[{_t(action.at)}] END SESSION"
    if isinstance(action, NoOp):
        return f"[{_t(action.at)}] NOOP ({action.reason})"
    return f"[{_t(action.at)}] {action.kind}"  # pragma: no cover


class ConsoleSink:
    """Pretty one-line-per-action output via ``rich``."""

    def __init__(self, roster: dict[str, str] | None = None, *, console: Console | None = None) -> None:
        self.roster: dict[str, str] = dict(roster) if roster else {}
        self._console = console or Console()

    def emit(self, actions: list[FacilitatorAction]) -> None:
        for action in actions:
            self._console.print(render_action(action, self.roster), markup=False, highlight=False)

    def close(self) -> None:
        pass


class JsonlSink:
    """Emit each step's action list as one JSON array per line (for assertions)."""

    def __init__(self, stream: TextIO | None = None, *, dump_state: bool = False) -> None:
        self._stream = stream or sys.stdout
        self.dump_state = dump_state
        self.final_state: object | None = None

    def emit(self, actions: list[FacilitatorAction]) -> None:
        payload = [a.model_dump(mode="json") for a in actions]
        self._stream.write(json.dumps(payload) + "\n")

    def close(self) -> None:
        if self.dump_state and self.final_state is not None:
            self._stream.write(json.dumps({"final_state": self.final_state}) + "\n")
