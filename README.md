# BrioCare

A real-time AI voice facilitator for group therapy sessions for kids — it augments a human clinician by handling session *mechanics*: turn-taking, prompting quieter participants, and maintaining the pace and flow of structured exercises. The clinician supervises and can override at any time.

## Status

Greenfield. First milestone (in planning): a declarative **exercise-script** format plus a deterministic **session state machine** that executes a script and emits facilitator actions, exercised via a **text/console simulation**. Voice (STT/TTS/realtime) is a later milestone designed to sit on top of this core without modifying it.

## Stack

Python 3.12+, pydantic v2, PyYAML, Typer, Rich, pytest (+ ruff, mypy), managed with `uv`.
