"""Fail-silent end-of-session JSON dump.

A tiny, dependency-free persistence seam: when a session ends, write the transcript,
feelings ratings, and generated notes to a JSON file so the parent summary and a
check-in/check-out record survive the in-memory room. **Never raises** — a failed
dump must not affect the live session.

Dump directory comes from ``BRIOCARE_DUMP_DIR`` (else ``/tmp/briocare``).

Caveat: these files contain real child PII (names, utterances, feelings). A retention
/ purge policy is a follow-up — this module only writes; it does not clean up.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def dump_dir() -> Path:
    return Path(os.getenv("BRIOCARE_DUMP_DIR") or "/tmp/briocare")


def dump_session(
    *,
    code: str,
    started_at: str | None,
    roster: dict[str, str],
    ratings: dict[str, dict[str, int]],
    transcript: list[dict[str, Any]],
    final_notes: str = "",
    parent_summaries: dict[str, dict[str, str]] | None = None,
) -> Path | None:
    """Write a session JSON snapshot. Returns the path on success, ``None`` on any failure.

    ``parent_summaries`` maps participant id -> {name, summary}; each summary is already
    privacy-scoped to that one child."""
    try:
        directory = dump_dir()
        directory.mkdir(parents=True, exist_ok=True)
        safe_code = "".join(c if (c.isalnum() or c in "-_") else "-" for c in code) or "session"
        stamp = (started_at or "session").replace(":", "-")
        path = directory / f"{safe_code}_{stamp}.json"
        payload = {
            "code": code,
            "started_at": started_at,
            "roster": roster,
            "ratings": ratings,
            "transcript": transcript,
            "final_notes": final_notes,
            "parent_summaries": parent_summaries or {},
        }
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        return path
    except Exception:
        return None
