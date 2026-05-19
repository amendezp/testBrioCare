"""In-memory simulation entry point used by the web demo.

Pure function: text in, structured result out — no HTTP, fully testable.
"""

from __future__ import annotations

from typing import Any

from briocare.io.facilitator_sink import render_action
from briocare.runtime.actions import FacilitatorAction
from briocare.scripts.loader import ScriptValidationError, loads_script
from briocare.sim.harness import SimulationHarness
from briocare.sim.transcript import TranscriptParseError


class _CollectSink:
    """Captures every step's actions and pretty-rendered lines."""

    def __init__(self) -> None:
        self.roster: dict[str, str] = {}
        self.steps: list[list[dict[str, Any]]] = []
        self.lines: list[str] = []
        self.final_state: dict[str, Any] | None = None

    def emit(self, actions: list[FacilitatorAction]) -> None:
        self.steps.append([a.model_dump(mode="json") for a in actions])
        self.lines.extend(render_action(a, self.roster) for a in actions)

    def close(self) -> None:
        pass


def simulate(script_text: str, transcript_text: str) -> dict[str, Any]:
    """Run a transcript through the machine; never raises for user input errors."""
    try:
        script = loads_script(script_text, source="<script>")
    except ScriptValidationError as exc:
        return {"ok": False, "error": str(exc), "stage": "script"}

    sink = _CollectSink()
    harness = SimulationHarness(script, sink)
    try:
        harness.run_transcript_text(transcript_text)
    except TranscriptParseError as exc:
        return {"ok": False, "error": str(exc), "stage": "transcript"}

    return {
        "ok": True,
        "script": {"id": script.id, "title": script.title, "phases": [p.id for p in script.phases]},
        "steps": sink.steps,
        "lines": sink.lines,
        "final_state": sink.final_state,
    }
