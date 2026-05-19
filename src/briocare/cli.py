"""Command-line entry point: ``briocare validate`` and ``briocare run``."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from briocare.io.facilitator_sink import ConsoleSink, JsonlSink
from briocare.scripts.loader import ScriptValidationError, load_script
from briocare.sim.harness import SimulationHarness

app = typer.Typer(add_completion=False, help="Session scaffolding + scripting engine for BrioCare.")
_err = Console(stderr=True)


@app.command()
def validate(script_path: Annotated[Path, typer.Argument(help="Exercise script (YAML/JSON).")]) -> None:
    """Validate an exercise script and report the first problem, if any."""
    try:
        script = load_script(script_path)
    except ScriptValidationError as exc:
        _err.print(f"[red]INVALID[/red]\n{exc}")
        raise typer.Exit(code=1) from exc
    typer.echo(f"OK: {script.id} — {script.title} ({len(script.phases)} phase(s))")


@app.command()
def run(
    script_path: Annotated[Path, typer.Argument(help="Exercise script (YAML/JSON).")],
    transcript: Annotated[Path | None, typer.Option(help="Transcript file; omit for an interactive REPL.")] = None,
    json_out: Annotated[bool, typer.Option("--json", help="Emit one JSON action-array per step (JSONL).")] = False,
    dump_state: Annotated[bool, typer.Option("--dump-state", help="With --json, append final state.")] = False,
    wpm: Annotated[float, typer.Option(help="Words-per-minute estimate for REPL utterance durations.")] = 130.0,
) -> None:
    """Run a script through the text simulation against a transcript or REPL."""
    try:
        script = load_script(script_path)
    except ScriptValidationError as exc:
        _err.print(f"[red]INVALID[/red]\n{exc}")
        raise typer.Exit(code=1) from exc

    sink: ConsoleSink | JsonlSink
    sink = JsonlSink(dump_state=dump_state) if json_out else ConsoleSink()
    harness = SimulationHarness(script, sink, wpm=wpm)

    if transcript is not None:
        harness.run_transcript_text(transcript.read_text(encoding="utf-8"))
    else:
        harness.run_repl()


def main() -> None:  # pragma: no cover - thin wrapper
    app()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(app())
