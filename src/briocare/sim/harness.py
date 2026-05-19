"""Drives a :class:`SessionMachine` from a transcript file or an interactive REPL.

The harness owns the :class:`LogicalClock`.  Between scheduled events it inspects
``machine.next_deadline()`` and, whenever a deadline falls before the next event,
advances the clock to it and feeds a ``SilenceTimeout`` / ``Tick`` — so phase
timers, wrap-up warnings, turn caps and quiet nudges all fire on schedule.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from typing import Protocol, TextIO, runtime_checkable

from briocare.runtime.actions import FacilitatorAction
from briocare.runtime.clock import LogicalClock
from briocare.runtime.events import (
    EndSessionRequest,
    InputEvent,
    ParticipantSpoke,
    SilenceTimeout,
    StartSession,
    Tick,
)
from briocare.runtime.machine import SessionMachine
from briocare.runtime.state import Lifecycle
from briocare.scripts.schema import ExerciseScript
from briocare.sim.transcript import TranscriptParseError, parse_event_body, parse_transcript

DEFAULT_WPM = 130.0
_MAX_DRAIN_TICKS = 100_000


@runtime_checkable
class SinkLike(Protocol):  # structural; real sinks just need .emit / .close
    def emit(self, actions: list[FacilitatorAction]) -> None: ...

    def close(self) -> None: ...


def _deadline_event(machine: SessionMachine, at: float) -> InputEvent:
    st = machine.state.phase
    silence = st is not None and st.current_turn is not None
    return SilenceTimeout(at=at) if silence else Tick(at=at)


class SimulationHarness:
    def __init__(self, script: ExerciseScript, sink: SinkLike, *, wpm: float = DEFAULT_WPM) -> None:
        self.script = script
        self.sink = sink
        self.wpm = wpm
        self.clock = LogicalClock()
        self.machine = SessionMachine(script, self.clock)

    # -- shared helpers -----------------------------------------------------

    def _emit(self, actions: list[FacilitatorAction]) -> None:
        if actions:
            self.sink.emit(actions)

    def _ended(self) -> bool:
        return self.machine.state.lifecycle == Lifecycle.ENDED

    def _finish(self) -> None:
        if hasattr(self.sink, "final_state"):
            self.sink.final_state = self.machine.state.model_dump(mode="json")
        self.sink.close()

    def _feed(self, event: InputEvent) -> None:
        self.clock.set(float(event.at))
        actions = self.machine.step(event)
        if isinstance(event, StartSession) and hasattr(self.sink, "roster"):
            self.sink.roster = dict(self.machine.state.roster)
        self._emit(actions)

    def _drain_until(self, target: float | None) -> None:
        """Feed ticks for every deadline strictly before ``target`` (or all, if None)."""
        guard = 0
        while not self._ended():
            d = self.machine.next_deadline()
            if d is None:
                return
            if target is not None and d >= target:
                return
            if d < self.clock.now():
                d = self.clock.now()
            self._feed(_deadline_event(self.machine, d))
            guard += 1
            if guard > _MAX_DRAIN_TICKS:  # pragma: no cover - safety net
                raise RuntimeError("deadline drain did not terminate")

    # -- transcript mode ----------------------------------------------------

    def run_events(self, events: Iterable[InputEvent]) -> None:
        for event in events:
            if self._ended():
                break
            self._drain_until(float(event.at))
            if self._ended():
                break
            self._feed(event)
        self._drain_until(None)
        self._finish()

    def run_transcript_text(self, text: str) -> None:
        parsed = parse_transcript(text)
        self.run_events(parsed.events)

    # -- REPL mode ----------------------------------------------------------

    def run_repl(self, *, input_stream: TextIO | None = None, output: TextIO | None = None) -> None:
        ins = input_stream or sys.stdin
        out = output or sys.stdout
        prompt = "briocare> "
        line_no = 0
        roster_seen: dict[str, str] = {}
        while True:
            if ins is sys.stdin and ins.isatty():
                out.write(prompt)
                out.flush()
            raw = ins.readline()
            if not raw:
                break
            line_no += 1
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            low = line.lower()
            if low in {"quit", "exit"}:
                break
            if low == "state":
                out.write(self.machine.state.model_dump_json(indent=2) + "\n")
                continue
            if low.startswith("roster:"):
                for chunk in line[len("roster:"):].split(","):
                    chunk = chunk.strip()
                    if "=" in chunk:
                        pid, name = chunk.split("=", 1)
                        roster_seen[pid.strip()] = name.strip()
                continue
            if low.startswith("/wait"):
                try:
                    dt = float(line.split(None, 1)[1])
                except (IndexError, ValueError):
                    out.write("usage: /wait <seconds>\n")
                    continue
                self._advance_repl(dt)
                continue
            try:
                event = parse_event_body(line_no, line, roster_seen)
            except TranscriptParseError as exc:
                out.write(f"parse error: {exc}\n")
                continue
            duration = self._utterance_seconds(event)
            self._feed(event.model_copy(update={"at": self.clock.now()}))
            if isinstance(event, EndSessionRequest) or self._ended():
                break
            self._advance_repl(duration)
        self._drain_until(None)
        self._finish()

    def _utterance_seconds(self, event: InputEvent) -> float:
        if isinstance(event, ParticipantSpoke) and event.text:
            words = max(1, len(event.text.split()))
            return round(words / self.wpm * 60.0, 3)
        return 0.0

    def _advance_repl(self, dt: float) -> None:
        target = self.clock.now() + max(0.0, dt)
        # drain deadlines that fall within the wait window, then jump to target
        self._drain_until(target)
        if self.clock.now() < target and not self._ended():
            self.clock.set(target)
