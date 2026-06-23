"""WebSocket message protocol for the live demo.

Client -> server messages are validated into typed pydantic models and mapped to
the engine's :class:`~briocare.runtime.events.InputEvent` types. Server -> client
messages are plain JSON dicts built by small helpers so the room code stays terse.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from briocare.runtime.events import (
    ClinicianOverride,
    EndSessionRequest,
    InputEvent,
    OverrideCommand,
)

# --- client -> server -------------------------------------------------------


class _ClientMsg(BaseModel):
    pass


class JoinMsg(_ClientMsg):
    """A kid announces presence with a display name (added to the lobby)."""

    type: Literal["join"] = "join"
    name: str


class StartMsg(_ClientMsg):
    type: Literal["start"] = "start"


class SpokeMsg(_ClientMsg):
    type: Literal["spoke"] = "spoke"
    text: str


class QuickReplyMsg(_ClientMsg):
    """A kid taps a feeling chip instead of speaking; auto-relayed to the group."""

    type: Literal["quick_reply"] = "quick_reply"
    text: str


class RatingMsg(_ClientMsg):
    """A kid submits a feelings-thermometer value during a rating phase."""

    type: Literal["rating"] = "rating"
    value: int


class PrivateNudgeMsg(_ClientMsg):
    """Therapist asks the co-pilot to send one child private encouragement."""

    type: Literal["private_nudge"] = "private_nudge"
    pid: str


class OverrideMsg(_ClientMsg):
    type: Literal["override"] = "override"
    command: OverrideCommand
    args: dict[str, str] = Field(default_factory=dict)


class EndMsg(_ClientMsg):
    type: Literal["end"] = "end"


ClientMessage = Annotated[
    JoinMsg | StartMsg | SpokeMsg | QuickReplyMsg | RatingMsg | PrivateNudgeMsg | OverrideMsg | EndMsg,
    Field(discriminator="type"),
]

_ADAPTER: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)


class ProtocolError(ValueError):
    """Raised when an inbound client message is malformed or not allowed for a role."""


def parse_client_message(raw: str) -> ClientMessage:
    try:
        return _ADAPTER.validate_json(raw)
    except ValidationError as exc:  # pragma: no cover - exercised via tests
        raise ProtocolError(str(exc)) from exc


def to_event(msg: ClientMessage) -> InputEvent:
    """Map an override/end message to an engine event (``at`` filled later).

    ``join`` / ``start`` / ``spoke`` are built by the room, which knows each
    connection's participant id and the full roster.
    """
    if isinstance(msg, OverrideMsg):
        return ClinicianOverride(at=0.0, command=msg.command, args=dict(msg.args))
    if isinstance(msg, EndMsg):
        return EndSessionRequest(at=0.0)
    raise ProtocolError(f"to_event does not handle {msg.type!r}")  # pragma: no cover


# --- server -> client builders ---------------------------------------------

# Roles
KID = "kid"
THERAPIST = "therapist"
ROLES = (KID, THERAPIST)


def room_info_msg(url: str | None) -> dict:
    """Daily room URL for the human video call (None -> video disabled)."""
    return {"type": "room_info", "url": url}


def identity_msg(*, pid: str, name: str) -> dict:
    """Tell a kid its assigned participant id so its UI can detect 'your turn'."""
    return {"type": "identity", "pid": pid, "name": name}


def transcript_msg(*, role: str, name: str, text: str, at: float, pid: str | None = None) -> dict:
    """One speaker-tagged line of the running transcript (therapist-facing)."""
    return {"type": "transcript", "role": role, "name": name, "pid": pid, "text": text, "at": at}


def cues_msg(cues: list[dict]) -> dict:
    """Plain, actionable co-pilot cues built from a step's engine actions."""
    return {"type": "cues", "cues": cues}


def actions_msg(actions_json: list[dict], lines: list[str]) -> dict:
    """The therapist's live action/cue feed (one rendered line per engine action)."""
    return {"type": "actions", "actions": actions_json, "lines": lines}


def assistant_msg(text: str) -> dict:
    """A friendly, kid-appropriate prompt shared with both roles."""
    return {"type": "assistant", "text": text}


def private_prompt_msg(text: str) -> dict:
    """Gentle, one-directional encouragement shown only on one child's screen."""
    return {"type": "private_prompt", "text": text}


def request_rating_msg(*, scale: int, prompt: str) -> dict:
    """Ask the kids to tap a feelings-thermometer value (rating phase)."""
    return {"type": "request_rating", "scale": scale, "prompt": prompt}


def session_review_msg(*, notes: str, kids: list[dict]) -> dict:
    """End-of-session review for the therapist 'lobby': the clinical note plus a
    per-child card (ratings, participation, that child's own transcript, and a
    privacy-scoped parent summary)."""
    return {"type": "session_review", "notes": notes, "kids": kids}


def notes_msg(markdown: str, *, final: bool = False) -> dict:
    """The latest AI session notes (therapist-facing)."""
    return {"type": "notes", "markdown": markdown, "final": final}


def snapshot_msg(state: dict) -> dict:
    return {"type": "snapshot", "state": state}


def session_over_msg() -> dict:
    return {"type": "session_over"}


def notice_msg(text: str) -> dict:
    return {"type": "notice", "text": text}
