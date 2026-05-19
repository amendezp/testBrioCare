from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from briocare.io.facilitator_sink import JsonlSink, render_action
from briocare.runtime.events import ClinicianOverride, OverrideCommand, ParticipantSpoke
from briocare.scripts.loader import load_script
from briocare.sim.harness import SimulationHarness
from briocare.sim.transcript import TranscriptParseError, parse_transcript
from tests.conftest import FIXTURES, LIBRARY


def _run_jsonl(script_path: Path, transcript_path: Path, *, dump_state: bool = False) -> str:
    script = load_script(script_path)
    buf = io.StringIO()
    sink = JsonlSink(buf, dump_state=dump_state)
    SimulationHarness(script, sink).run_transcript_text(transcript_path.read_text())
    return buf.getvalue()


def test_transcript_parsing_resolves_times_and_overrides() -> None:
    parsed = parse_transcript(
        "roster: a=Ann, b=Bo\n@0 start\n+5 a: hello there\n+3 >> say \"take a breath\"\n+2 b: pass\n"
    )
    assert parsed.roster == {"a": "Ann", "b": "Bo"}
    ats = [e.at for e in parsed.events]
    assert ats == [0.0, 5.0, 8.0, 10.0]
    spoke = parsed.events[1]
    assert isinstance(spoke, ParticipantSpoke) and spoke.text == "hello there"
    inj = parsed.events[2]
    assert isinstance(inj, ClinicianOverride)
    assert inj.command == OverrideCommand.INJECT_PROMPT
    assert inj.args["text"] == "take a breath"


def test_malformed_line_reports_line_number() -> None:
    with pytest.raises(TranscriptParseError) as exc:
        parse_transcript("roster: a=Ann\n@0 start\n+1 ??garbage??\n")
    assert exc.value.line_no == 3
    assert "line 3" in str(exc.value)


def test_happy_path_is_deterministic_across_runs() -> None:
    a = _run_jsonl(LIBRARY / "feelings_checkin_circle.yaml", FIXTURES / "transcript_happy_path.txt")
    b = _run_jsonl(LIBRARY / "feelings_checkin_circle.yaml", FIXTURES / "transcript_happy_path.txt")
    assert a == b
    # every emitted line is a JSON array of actions
    lines = [json.loads(line) for line in a.splitlines()]
    assert lines and all(isinstance(step, list) for step in lines)
    flat = [act for step in lines for act in step]
    sources = {a["source"] for a in flat if a["kind"] == "say_prompt"}
    assert {"intro", "phase_opening", "closing"} <= sources


def test_quiet_kid_transcript_produces_quiet_nudges() -> None:
    out = _run_jsonl(LIBRARY / "feelings_checkin_circle.yaml", FIXTURES / "transcript_quiet_kid.txt")
    flat = [act for line in out.splitlines() for act in json.loads(line)]
    nudges = [a for a in flat if a["kind"] == "invite_participant" and a["reason"] == "quiet_nudge"]
    assert nudges, "expected at least one quiet nudge for the silent kid"
    assert all(a["participant_id"] == "kid4" for a in nudges)


def test_override_transcript_shows_mute_and_inject() -> None:
    out = _run_jsonl(LIBRARY / "feelings_checkin_circle.yaml", FIXTURES / "transcript_override.txt")
    flat = [act for line in out.splitlines() for act in json.loads(line)]
    # the injected prompt survives even though the agent was muted earlier
    injected = [a for a in flat if a["kind"] == "say_prompt" and a["source"] == "injected"]
    assert injected and injected[0]["text"] == "Let's all take a slow breath together."
    # at least one muted spoken action was downgraded to a NoOp
    assert any(a["kind"] == "no_op" and "agent_muted" in a["reason"] for a in flat)


def test_dump_state_appends_final_state() -> None:
    out = _run_jsonl(
        LIBRARY / "feelings_checkin_circle.yaml",
        FIXTURES / "transcript_happy_path.txt",
        dump_state=True,
    )
    last = json.loads(out.splitlines()[-1])
    assert "final_state" in last
    assert last["final_state"]["lifecycle"] == "ended"


def test_render_action_one_line_format() -> None:
    from briocare.runtime.actions import InviteParticipant, InviteReason

    line = render_action(
        InviteParticipant(
            at=15.0, participant_id="kid1", text="hi", reason=InviteReason.QUIET_NUDGE,
            attempt=1, max_attempts=2,
        ),
        {"kid1": "Maya"},
    )
    assert line == "[t=15] QUIET-NUDGE Maya (attempt 1/2)"
