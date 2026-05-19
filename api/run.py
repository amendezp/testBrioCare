"""Vercel Python serverless function backing the BrioCare web demo.

GET  /api/run  -> { script, transcript }  (the bundled defaults, for prefill)
POST /api/run  { script, transcript } -> simulation result (see websim.simulate)
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from briocare.websim import simulate  # noqa: E402

_LIB = _ROOT / "src" / "briocare" / "scripts" / "library" / "feelings_checkin_circle.yaml"
_TRANSCRIPT = _ROOT / "tests" / "fixtures" / "transcript_happy_path.txt"


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _defaults() -> dict[str, str]:
    return {"script": _read(_LIB), "transcript": _read(_TRANSCRIPT)}


class handler(BaseHTTPRequestHandler):
    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None: # required Vercel handler name
        self._send(200, _defaults())

    def do_POST(self) -> None: # required Vercel handler name
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self._send(400, {"ok": False, "error": "request body must be JSON"})
            return
        script = data.get("script", "")
        transcript = data.get("transcript", "")
        if not script.strip() or not transcript.strip():
            self._send(400, {"ok": False, "error": "both 'script' and 'transcript' are required"})
            return
        result = simulate(script, transcript)
        self._send(200 if result.get("ok") else 422, result)
