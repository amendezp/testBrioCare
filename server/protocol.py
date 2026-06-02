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
    ParticipantSpoke,
    StartSession,
)

# --- client -> server -------------------------------------------------------


class _ClientMsg(BaseModel):
    pass


class StartMsg(_ClientMsg):
    type: Literal["start"] = "start"
    kid_name: str | None = None


class SpokeMsg(_ClientMsg):
    type: Literal["spoke"] = "spoke"
    text: str


class OverrideMsg(_ClientMsg):
    type: Literal["override"] = "override"
    command: OverrideCommand
    args: dict[str, str] = Field(default_factory=dict)


class EndMsg(_ClientMsg):
    type: Literal["end"] = "end"


ClientMessage = Annotated[
    StartMsg | SpokeMsg | OverrideMsg | EndMsg,
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


def to_event(
    msg: ClientMessage,
    *,
    kid_pid: str,
    kid_name: str,
) -> InputEvent:
    """Map a validated client message to an engine input event (``at`` filled later)."""
    if isinstance(msg, StartMsg):
        name = (msg.kid_name or kid_name).strip() or kid_name
        return StartSession(at=0.0, roster={kid_pid: name})
    if isinstance(msg, SpokeMsg):
        return ParticipantSpoke(at=0.0, participant_id=kid_pid, text=msg.text)
    if isinstance(msg, OverrideMsg):
        return ClinicianOverride(at=0.0, command=msg.command, args=dict(msg.args))
    if isinstance(msg, EndMsg):
        return EndSessionRequest(at=0.0)
    raise ProtocolError(f"unhandled message type: {msg!r}")  # pragma: no cover


# --- server -> client builders ---------------------------------------------

# Roles
KID = "kid"
THERAPIST = "therapist"
ROLES = (KID, THERAPIST)


def room_info_msg(url: str | None) -> dict:
    """Daily room URL for the human video call (None -> video disabled)."""
    return {"type": "room_info", "url": url}


def transcript_msg(*, role: str, name: str, text: str, at: float) -> dict:
    """One speaker-tagged line of the running transcript (therapist-facing)."""
    return {"type": "transcript", "role": role, "name": name, "text": text, "at": at}


def actions_msg(actions_json: list[dict], lines: list[str]) -> dict:
    """The therapist's live action/cue feed (one rendered line per engine action)."""
    return {"type": "actions", "actions": actions_json, "lines": lines}


def assistant_msg(text: str) -> dict:
    """A friendly, kid-appropriate prompt shared with both roles."""
    return {"type": "assistant", "text": text}


def notes_msg(markdown: str, *, final: bool = False) -> dict:
    """The latest AI session notes (therapist-facing)."""
    return {"type": "notes", "markdown": markdown, "final": final}


def snapshot_msg(state: dict) -> dict:
    return {"type": "snapshot", "state": state}


def session_over_msg() -> dict:
    return {"type": "session_over"}


def notice_msg(text: str) -> dict:
    return {"type": "notice", "text": text}
