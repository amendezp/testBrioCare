"""FastAPI entrypoint for the telehealth co-pilot demo.

Routes:
- ``GET /``            role chooser (landing page)
- ``GET /kid``         child view (video + shared activity)
- ``GET /therapist``   therapist console (video + transcript + AI notes + cues + controls)
- ``GET /healthz``     Railway healthcheck
- ``WS  /ws/{room}/{role}``  the live session socket (role ∈ {kid, therapist})

Run locally:  ``uv run --extra server uvicorn server.app:app --reload --port 8000``
On Railway:   ``uvicorn server.app:app --host 0.0.0.0 --port $PORT``

Optional env (each degrades gracefully if unset):
- ``DAILY_API_KEY``      → live video between the two humans
- ``ANTHROPIC_API_KEY``  → AI notes + shared-prompt phrasing
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:  # mirror api/run.py so the bundled engine imports cleanly
    sys.path.insert(0, str(_SRC))


def _load_dotenv() -> None:
    """Load ``KEY=value`` lines from a local ``.env`` (no dependency, never overrides real env)."""
    env_path = _ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


_load_dotenv()  # so DAILY_API_KEY / ANTHROPIC_API_KEY in .env are picked up before services init

from fastapi import FastAPI, WebSocket, WebSocketDisconnect  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402

from briocare.scripts.loader import load_script  # noqa: E402
from server import protocol  # noqa: E402
from server.daily import Daily  # noqa: E402
from server.notes import NoteTaker  # noqa: E402
from server.phraser import Phraser  # noqa: E402
from server.room import SessionRoom  # noqa: E402

_STATIC = Path(__file__).resolve().parent / "static"
_GROUP_SCRIPT = Path(__file__).resolve().parent / "scripts" / "group_checkin.yaml"

app = FastAPI(title="BrioCare Telehealth Co-Pilot")

_phraser = Phraser()
_notes = NoteTaker()
_daily = Daily()
_rooms: dict[str, SessionRoom] = {}


def _get_or_create_room(code: str) -> SessionRoom:
    room = _rooms.get(code)
    if room is None:
        room = SessionRoom(
            code,
            load_script(_GROUP_SCRIPT),
            phraser=_phraser,
            notes=_notes,
            daily=_daily,
        )
        _rooms[code] = room
    return room


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "notes": _notes.enabled, "video": _daily.enabled, "phraser": _phraser.enabled}


@app.get("/")
def landing() -> FileResponse:
    return FileResponse(_STATIC / "landing.html")


@app.get("/kid")
def kid_page() -> FileResponse:
    return FileResponse(_STATIC / "kid.html")


@app.get("/therapist")
def therapist_page() -> FileResponse:
    return FileResponse(_STATIC / "therapist.html")


@app.websocket("/ws/{room}/{role}")
async def ws_endpoint(ws: WebSocket, room: str, role: str) -> None:
    if role not in protocol.ROLES:
        await ws.close(code=4404)
        return
    await ws.accept()
    session = _get_or_create_room(room)
    if not await session.attach(role, ws):
        await ws.send_json(protocol.notice_msg(f"This session already has a {role}."))
        await ws.close(code=4409)
        return
    try:
        while True:
            raw = await ws.receive_text()
            await session.handle_client_message(role, ws, raw)
    except WebSocketDisconnect:
        pass
    finally:
        await session.detach(role, ws)
