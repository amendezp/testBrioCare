"""Client-message parsing + mapping to engine input events."""

from __future__ import annotations

import pytest
from server import protocol

from briocare.runtime.events import ClinicianOverride, EndSessionRequest, OverrideCommand


def test_parse_join() -> None:
    msg = protocol.parse_client_message('{"type":"join","name":"Maya"}')
    assert isinstance(msg, protocol.JoinMsg)
    assert msg.name == "Maya"


def test_parse_spoke() -> None:
    msg = protocol.parse_client_message('{"type":"spoke","text":"i feel happy"}')
    assert isinstance(msg, protocol.SpokeMsg)
    assert msg.text == "i feel happy"


def test_parse_start() -> None:
    assert isinstance(protocol.parse_client_message('{"type":"start"}'), protocol.StartMsg)


def test_override_maps_to_event() -> None:
    msg = protocol.parse_client_message('{"type":"override","command":"goto_phase","args":{"phase_id":"reflect"}}')
    event = protocol.to_event(msg)
    assert isinstance(event, ClinicianOverride)
    assert event.command == OverrideCommand.GOTO_PHASE
    assert event.args == {"phase_id": "reflect"}


def test_end_maps_to_event() -> None:
    assert isinstance(protocol.to_event(protocol.parse_client_message('{"type":"end"}')), EndSessionRequest)


def test_to_event_rejects_room_built_messages() -> None:
    # start / spoke / join are built by the room (it knows the connection's pid + roster)
    with pytest.raises(protocol.ProtocolError):
        protocol.to_event(protocol.parse_client_message('{"type":"start"}'))


def test_unknown_type_is_protocol_error() -> None:
    with pytest.raises(protocol.ProtocolError):
        protocol.parse_client_message('{"type":"nonsense"}')


def test_bad_override_command_is_protocol_error() -> None:
    with pytest.raises(protocol.ProtocolError):
        protocol.parse_client_message('{"type":"override","command":"frobnicate"}')


def test_identity_builder() -> None:
    assert protocol.identity_msg(pid="kid2", name="Leo") == {"type": "identity", "pid": "kid2", "name": "Leo"}


def test_transcript_includes_pid() -> None:
    m = protocol.transcript_msg(role="kid", name="Maya", text="hi", at=1.0, pid="kid1")
    assert m["type"] == "transcript" and m["pid"] == "kid1" and m["name"] == "Maya"


def test_cues_builder() -> None:
    m = protocol.cues_msg([{"icon": "👉", "text": "Leo's turn", "level": "action"}])
    assert m["type"] == "cues" and m["cues"][0]["text"] == "Leo's turn"
