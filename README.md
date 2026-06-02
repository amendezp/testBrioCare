# BrioCare

A real-time AI voice facilitator for group therapy sessions for kids — it augments a human clinician by handling session *mechanics*: turn-taking, prompting quieter participants, and maintaining the pace and flow of structured exercises. The clinician supervises and can override at any time.

## Status

First milestone delivered: a declarative **exercise-script** format plus a synchronous, deterministic **session state machine** that executes a script and emits `FacilitatorAction`s, exercised via a **text/console simulation**. There is no audio yet — voice (STT/TTS/realtime, Pipecat) is a later milestone designed to sit on top of this core via three reused Protocols (`ParticipantSource`, `FacilitatorSink`, `Clock`) **without modifying `runtime/`**. See [`docs/PLAN.md`](docs/PLAN.md) for the full design and diagrams.

## Stack

Python 3.12+, pydantic v2, PyYAML, Typer, Rich, pytest (+ ruff, mypy), managed with `uv`. No `asyncio` in this milestone — the machine is *pulled* (event in → actions out), which keeps tests deterministic.

## Install

```bash
uv sync
```

## Usage

Validate a script:

```bash
uv run briocare validate src/briocare/scripts/library/feelings_checkin_circle.yaml
```

Run the bundled "Feelings Check-in Circle" against a transcript (console output):

```bash
uv run briocare run src/briocare/scripts/library/feelings_checkin_circle.yaml \
    --transcript tests/fixtures/transcript_happy_path.txt
```

Sample output (one line per realized facilitator action):

```
[t=0] SAY (intro): "Hi everyone, welcome back to our circle. ..."
[t=0] SAY (phase_opening:model_and_warmup): "Let's warm up. ..."
[t=70] SAY (wrapup_warning:model_and_warmup): "Let's start wrapping up this part ..."
[t=90] WRAPUP model_and_warmup
[t=90] ADVANCE model_and_warmup -> go_around
[t=90] INVITE Maya (round_robin_turn)
[t=95] ACK Maya: "Thank you, Maya."
...
[t=208] SAY (closing): "Thanks for sharing, everyone. ..."
```

Machine-readable mode (one JSON array of actions per `step`, deterministic across runs):

```bash
uv run briocare run <script> --transcript <file> --json [--dump-state]
```

Interactive REPL (omit `--transcript`); accepts `<pid>: <text>`, `/wait <seconds>`,
`>> <override>` (e.g. `>> mute`, `>> say "..."`, `>> advance`), `start`, `end`,
`state`, `quit`.

## Transcript format

```
roster: kid1=Maya, kid2=Leo, kid3=Aisha, kid4=Sam
@0  start                       # @T = absolute seconds, +D = delta (default +0)
+2  kid2: I feel nervous        # pid: text  -> ParticipantSpoke
+45 kid1: pass                  # literal "pass" / "<pass>" -> pass
+5  >> advance                  # >> cmd     -> ClinicianOverride
+20 >> end
```

## Web demo

A minimal browser playground (Vercel) lets you edit a script + transcript and see
the facilitator-action stream. It's a thin layer over the same engine:
`public/index.html` → `api/run.py` (Python serverless) → `briocare.websim.simulate`.
No audio; the core is unchanged.

## Live telehealth co-pilot demo (`server/`)

A real-time, two-**human** session for showing the product to therapists and investors.
Open `/` and pick **Therapist** or **Kid**; both join one live session. The therapist
and child talk to each other over a **Daily** video call — the AI is **not** a therapist,
it's a **co-pilot** riding along to make the human session more efficient:

- **Live transcript** — each browser transcribes its own mic (browser-native Web Speech,
  **Chrome/Edge**) and streams speaker-tagged text to the therapist console.
- **AI session notes** — Claude turns the running transcript into structured, editable
  clinical notes during the session and a fuller summary at the end (`server/notes.py`).
- **Session-mechanics cues** — the turn-taking / pacing / quiet-detection engine, surfaced
  as gentle on-screen cues for the therapist (driven only by the **child's** utterances).
  The therapist keeps full control (start, next activity, pause, mute cues, skip, share a
  prompt, end).

It's an additive layer over the unchanged engine. `server.room.SessionRoom` holds one
`SessionMachine` + a real `WallClock` per room, drives `Tick`/`SilenceTimeout` off a single
asyncio timer (mirroring the sim harness), and pushes to both browsers over WebSockets.
Both integrations **degrade gracefully**: no `DAILY_API_KEY` → video panel disabled (rest
works); no `ANTHROPIC_API_KEY` → notes show the raw transcript.

```bash
# Run locally (two Chrome tabs at http://localhost:8000 — therapist + kid):
uv run --extra server uvicorn server.app:app --reload --port 8000
export DAILY_API_KEY=...            # optional; enables the human video call
export ANTHROPIC_API_KEY=sk-...     # optional; enables AI notes + shared-prompt phrasing
```

Deploy on **Railway** (persistent process; HTTPS by default, which the mic + camera need):
the repo ships `nixpacks.toml` + `railway.json` + `Procfile`, so it builds and runs as-is —
just set `DAILY_API_KEY` and `ANTHROPIC_API_KEY` in the Variables tab. See
[`RAILWAY.md`](RAILWAY.md) for step-by-step. Activities come from `server/scripts/solo_checkin.yaml`.

## Development

```bash
uv run pytest                       # full deterministic test suite
uv run ruff check src tests         # lint
uv run mypy src/briocare            # strict type-check
```

## Layout

```
src/briocare/
  scripts/   schema + YAML/JSON loader + bundled library script
  runtime/   clock, events, actions, state, policies, SessionMachine
  sim/       transcript parser + simulation harness
  io/        FacilitatorSink / ParticipantSource Protocols + console/JSONL/REPL impls
  cli.py     `briocare validate` / `briocare run`
```
