"""Daily.co video rooms for the human-to-human telehealth call.

One room per session code. The AI co-pilot does *not* need server-side access to the
audio (transcription comes from each browser's Web Speech), so all we need from Daily
is a room URL both browsers can join.

**Fails open**: with no ``DAILY_API_KEY`` (or any API error) ``get_or_create_room``
returns ``None`` and the UI simply shows the video panel as disabled — the rest of the
demo (transcript, cues, notes) runs unchanged.
"""

from __future__ import annotations

import os
import re
import time

_API = "https://api.daily.co/v1/rooms"
_ROOM_TTL_SECONDS = 2 * 60 * 60
_SAFE = re.compile(r"[^a-zA-Z0-9_-]")


def _room_name(code: str) -> str:
    slug = _SAFE.sub("-", code).strip("-") or "demo"
    return f"briocare-{slug}"[:128]


class Daily:
    def __init__(self) -> None:
        self._key = os.getenv("DAILY_API_KEY")
        self._cache: dict[str, str] = {}

    @property
    def enabled(self) -> bool:
        return self._key is not None

    async def get_or_create_room(self, code: str) -> str | None:
        """Return a joinable Daily room URL for ``code``, or ``None`` if unavailable."""
        if self._key is None:
            return None
        if code in self._cache:
            return self._cache[code]

        try:
            import httpx
        except ImportError:  # pragma: no cover - httpx ships with the server extra
            return None

        name = _room_name(code)
        headers = {"Authorization": f"Bearer {self._key}"}
        properties = {
            "exp": int(time.time()) + _ROOM_TTL_SECONDS,
            "enable_chat": False,
            "enable_prejoin_ui": False,
            "start_audio_off": True,  # everyone joins muted; they tap to be heard
            "start_video_off": False,
        }
        body = {"name": name, "privacy": "public", "properties": properties}
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.post(_API, headers=headers, json=body)
                if resp.status_code == 200:
                    url = resp.json().get("url")
                elif resp.status_code == 400:
                    # Room already exists — update it so the (possibly older) room
                    # also starts muted, then use its url.
                    upd = await client.post(
                        f"{_API}/{name}", headers=headers, json={"properties": properties}
                    )
                    if upd.status_code == 200:
                        url = upd.json().get("url")
                    else:
                        got = await client.get(f"{_API}/{name}", headers=headers)
                        url = got.json().get("url") if got.status_code == 200 else None
                else:
                    url = None
        except Exception:
            return None

        if url:
            self._cache[code] = url
        return url
