from __future__ import annotations

from briocare.websim import simulate
from tests.conftest import FIXTURES, LIBRARY

SCRIPT = (LIBRARY / "feelings_checkin_circle.yaml").read_text()
HAPPY = (FIXTURES / "transcript_happy_path.txt").read_text()


def test_simulate_happy_path_ok_and_deterministic() -> None:
    a = simulate(SCRIPT, HAPPY)
    b = simulate(SCRIPT, HAPPY)
    assert a["ok"] is True
    assert a == b
    assert a["script"]["id"] == "feelings_checkin_circle"
    assert a["lines"][0].startswith("[t=0] SAY (intro)")
    sources = {s["source"] for step in a["steps"] for s in step if s["kind"] == "say_prompt"}
    assert {"intro", "phase_opening", "closing"} <= sources


def test_simulate_reports_script_error() -> None:
    r = simulate("phases: []\n", HAPPY)
    assert r["ok"] is False and r["stage"] == "script"
    assert "phases" in r["error"]


def test_simulate_reports_transcript_error() -> None:
    r = simulate(SCRIPT, "roster: a=Ann\n@0 start\n+1 ??bad??\n")
    assert r["ok"] is False and r["stage"] == "transcript"
    assert "line 3" in r["error"]
