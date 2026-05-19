"""Input side of the loop.

``ParticipantSource`` is one of the three Protocols the future voice app reuses
verbatim (alongside :class:`~briocare.io.facilitator_sink.FacilitatorSink` and
:class:`~briocare.runtime.clock.Clock`).  Today's concrete sources read a
transcript file or a REPL; the future ``STTSource`` will yield the same
``ParticipantSpoke`` events from finalized ASR + speaker diarization, with the
diarization↔participant mapping done upstream — the event shape is unchanged.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from typing import Protocol, TextIO, runtime_checkable

from briocare.runtime.events import InputEvent
from briocare.sim.transcript import ParsedTranscript, parse_event_body, parse_transcript


@runtime_checkable
class ParticipantSource(Protocol):
    """Yields input events (chiefly ``ParticipantSpoke``) in session order."""

    def events(self) -> Iterator[InputEvent]: ...


class TranscriptSource:
    """A pre-parsed transcript replayed as a fixed timeline of events."""

    def __init__(self, parsed: ParsedTranscript) -> None:
        self.parsed = parsed

    @classmethod
    def from_text(cls, text: str) -> TranscriptSource:
        return cls(parse_transcript(text))

    @property
    def roster(self) -> dict[str, str]:
        return dict(self.parsed.roster)

    def events(self) -> Iterator[InputEvent]:
        yield from self.parsed.events


class ReplSource:
    """Reads transcript-style lines from a stream, one event at a time.

    Time is *not* encoded in REPL lines — the harness assigns timestamps from
    its logical clock — so the yielded events carry ``at == 0.0``.
    """

    def __init__(self, stream: TextIO | None = None) -> None:
        self.stream = stream or sys.stdin
        self.roster: dict[str, str] = {}

    def events(self) -> Iterator[InputEvent]:
        line_no = 0
        for raw in self.stream:
            line_no += 1
            line = raw.strip()
            if not line or line.startswith("#") or line.lower() in {"quit", "exit"}:
                if line.lower() in {"quit", "exit"}:
                    return
                continue
            if line.lower().startswith("roster:"):
                for chunk in line[len("roster:"):].split(","):
                    if "=" in chunk:
                        pid, name = chunk.split("=", 1)
                        self.roster[pid.strip()] = name.strip()
                continue
            yield parse_event_body(line_no, line, self.roster)
