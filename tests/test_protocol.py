"""Client-message parsing + mapping to engine input events."""

from __future__ import annotations

import pytest
from server import protocol

from briocare.runtime.events import (
    ClinicianOverride,
    EndSessionRequest,
    OverrideCommand,
    ParticipantSpoke,
    StartSession,
)


def test_start_message_maps_to_start_session() -> None:
    msg = protocol.parse_client_message('{"type":"start","kid_name":"Maya"}')
    event = protocol.to_event(msg, kid_pid="kid1", kid_name="Friend")
    assert isinstance(event, StartSession)
    assert event.roster == {"kid1": "Maya"}


def test_start_message_defaults_kid_name() -> None:
    msg = protocol.parse_client_message('{"type":"start"}')
    event = protocol.to_event(msg, kid_pid="kid1", kid_name="Friend")
    assert isinstance(event, StartSession)
    assert event.roster == {"kid1": "Friend"}


def test_spoke_message_maps_to_participant_spoke() -> None:
    msg = protocol.parse_client_message('{"type":"spoke","text":"i feel happy"}')
    event = protocol.to_event(msg, kid_pid="kid1", kid_name="Friend")
    assert isinstance(event, ParticipantSpoke)
    assert event.participant_id == "kid1"
    assert event.text == "i feel happy"


def test_override_message_maps_command_and_args() -> None:
    msg = protocol.parse_client_message('{"type":"override","command":"goto_phase","args":{"phase_id":"reflect"}}')
    event = protocol.to_event(msg, kid_pid="kid1", kid_name="Friend")
    assert isinstance(event, ClinicianOverride)
    assert event.command == OverrideCommand.GOTO_PHASE
    assert event.args == {"phase_id": "reflect"}


def test_end_message_maps_to_end_request() -> None:
    msg = protocol.parse_client_message('{"type":"end"}')
    event = protocol.to_event(msg, kid_pid="kid1", kid_name="Friend")
    assert isinstance(event, EndSessionRequest)


def test_unknown_type_is_protocol_error() -> None:
    with pytest.raises(protocol.ProtocolError):
        protocol.parse_client_message('{"type":"nonsense"}')


def test_bad_override_command_is_protocol_error() -> None:
    with pytest.raises(protocol.ProtocolError):
        protocol.parse_client_message('{"type":"override","command":"frobnicate"}')
