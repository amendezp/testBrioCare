"""Parser for the plain-text transcript / REPL line format.

Grammar (blank lines and ``#``-comments are ignored)::

    line        := comment | roster_line | event_line
    roster_line := "roster:" pair ("," pair)*
    pair        := id "=" name
    event_line  := time? token rest?
    time        := "@" number | "+" number      # absolute / delta seconds; absent => "+0"
    token       := id ":" | ">>" | "start" | "end"
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field

from briocare.runtime.events import (
    ClinicianOverride,
    EndSessionRequest,
    InputEvent,
    OverrideCommand,
    ParticipantSpoke,
    StartSession,
)

_PASS_LITERALS = {"pass", "<pass>"}

_OVERRIDE_MAP: dict[str, OverrideCommand] = {
    "advance": OverrideCommand.ADVANCE_PHASE,
    "goto": OverrideCommand.GOTO_PHASE,
    "set-turn": OverrideCommand.SET_TURN,
    "skip": OverrideCommand.SKIP_PARTICIPANT,
    "pause": OverrideCommand.PAUSE,
    "resume": OverrideCommand.RESUME,
    "mute": OverrideCommand.MUTE_AGENT,
    "unmute": OverrideCommand.UNMUTE_AGENT,
    "say": OverrideCommand.INJECT_PROMPT,
    "invite": OverrideCommand.SET_TURN,
    "end": OverrideCommand.END_SESSION,
}


class TranscriptParseError(ValueError):
    """Raised on a malformed transcript line; carries the 1-based line number."""

    def __init__(self, line_no: int, message: str) -> None:
        super().__init__(f"line {line_no}: {message}")
        self.line_no = line_no


@dataclass
class ParsedTranscript:
    roster: dict[str, str] = field(default_factory=dict)
    events: list[InputEvent] = field(default_factory=list)


_TIME_RE = re.compile(r"^([@+])(-?\d+(?:\.\d+)?)\s+(.*)$")


def parse_override(line_no: int, body: str) -> ClinicianOverride:
    """Parse the part after ``>>`` into a ClinicianOverride (``at`` unset → 0.0)."""
    parts = body.split(None, 1)
    if not parts:
        raise TranscriptParseError(line_no, "empty override command")
    cmd_word = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""
    if cmd_word not in _OVERRIDE_MAP:
        raise TranscriptParseError(line_no, f"unknown override command {cmd_word!r}")
    command = _OVERRIDE_MAP[cmd_word]
    args: dict[str, str] = {}
    if command == OverrideCommand.GOTO_PHASE:
        if not rest:
            raise TranscriptParseError(line_no, "goto requires a phase id")
        args["phase_id"] = rest.split()[0]
    elif command == OverrideCommand.SET_TURN:
        if not rest:
            raise TranscriptParseError(line_no, f"{cmd_word} requires a participant id")
        args["pid"] = rest.split()[0]
    elif command == OverrideCommand.SKIP_PARTICIPANT:
        if not rest:
            raise TranscriptParseError(line_no, "skip requires a participant id")
        args["pid"] = rest.split()[0]
    elif command == OverrideCommand.INJECT_PROMPT:
        if not rest:
            raise TranscriptParseError(line_no, "say requires text")
        try:
            tokens = shlex.split(rest)
        except ValueError:
            tokens = [rest.strip('"').strip("'")]
        args["text"] = " ".join(tokens) if tokens else rest
    return ClinicianOverride(at=0.0, command=command, args=args)


def _parse_roster(line_no: int, rest: str) -> dict[str, str]:
    roster: dict[str, str] = {}
    for chunk in rest.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" not in chunk:
            raise TranscriptParseError(line_no, f"roster entry {chunk!r} must be id=name")
        pid, name = chunk.split("=", 1)
        pid, name = pid.strip(), name.strip()
        if not pid or not name:
            raise TranscriptParseError(line_no, f"roster entry {chunk!r} must be id=name")
        roster[pid] = name
    if not roster:
        raise TranscriptParseError(line_no, "empty roster")
    return roster


def parse_event_body(line_no: int, body: str, roster: dict[str, str]) -> InputEvent:
    """Parse a single event (without time prefix); ``at`` is left at 0.0."""
    body = body.strip()
    if body == "start":
        if not roster:
            raise TranscriptParseError(line_no, "'start' before any roster: line")
        return StartSession(at=0.0, roster=dict(roster))
    if body == "end":
        return EndSessionRequest(at=0.0)
    if body.startswith(">>"):
        return parse_override(line_no, body[2:].strip())
    m = re.match(r"^([A-Za-z0-9_\-]+):\s?(.*)$", body)
    if m:
        pid, text = m.group(1), m.group(2).strip()
        return ParticipantSpoke(at=0.0, participant_id=pid, text=text)
    raise TranscriptParseError(line_no, f"unrecognised line: {body!r}")


def parse_transcript(text: str) -> ParsedTranscript:
    out = ParsedTranscript()
    now = 0.0
    for i, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("roster:"):
            out.roster.update(_parse_roster(i, line[len("roster:"):]))
            continue
        m = _TIME_RE.match(line)
        if m:
            kind, value, body = m.group(1), float(m.group(2)), m.group(3)
            now = value if kind == "@" else now + value
        else:
            body = line
        event = parse_event_body(i, body, out.roster)
        out.events.append(event.model_copy(update={"at": now}))
    return out
