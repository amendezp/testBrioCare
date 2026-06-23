"""AI note-taker: turn the running session transcript into clinical notes.

Produces live, structured notes during the session (refreshed as the conversation
grows) and a fuller summary at the end. These are an aid for the **human therapist**,
never a clinical record of record — the UI keeps them editable.

**Fails open**: without ``ANTHROPIC_API_KEY`` (or on any error) it returns a plain
rendering of the transcript so the therapist still sees the conversation.
"""

from __future__ import annotations

import os
from typing import Any

MODEL = "claude-sonnet-4-6"
_TIMEOUT_SECONDS = 8.0

_SYSTEM_LIVE = (
    "You are a clinical scribe assisting a licensed child therapist during a live "
    "telehealth session with a young client (ages 7-11). You are given the running "
    "transcript so far. Produce concise, well-organized session notes in Markdown with "
    "these sections, omitting any that have no content yet:\n"
    "## Summary\n## Feelings & themes observed\n## Activities\n## Notable moments\n"
    "## Suggested follow-ups\n\n"
    "Be factual and observational, use the child's words where telling, and never "
    "diagnose or invent details not supported by the transcript. Keep it tight."
)
_SYSTEM_FINAL = (
    "You are a clinical scribe writing the end-of-session note for a licensed child "
    "therapist after a telehealth session with a young client (ages 7-11). Given the full "
    "transcript, write a clear Markdown note with:\n"
    "## Session summary\n## Feelings & themes\n## Activities completed\n## Notable moments\n"
    "## Goals touched\n## Suggested follow-ups for next session\n\n"
    "Be factual and observational, quote the child where telling, never diagnose or invent "
    "details. This is a draft for the therapist to edit."
)
_SYSTEM_PARENT_KID = (
    "You are writing a short, warm note to the parent or guardian of ONE child after a "
    "clinician-led group session. You are given ONLY that child's own words/feelings, a "
    "generic list of the group's activities, and a note on how much they took part. Use "
    "plain, encouraging language — no clinical jargon, no diagnosis, no labels. Write 2-3 "
    "short paragraphs: how their child took part and any feelings they shared, set against "
    "light, generic group context, plus one gentle suggestion for home. Address the parent "
    "directly ('Today, your child…').\n\n"
    "CRITICAL PRIVACY RULES: This note is for one family only. Do NOT use any first names at "
    "all — refer to the subject as 'your child' and to any peer only as 'a friend' or 'the "
    "group'. Even if the child's own words mention another person, a sibling, a teacher, a "
    "place, a street, a school, or any other identifying detail, OMIT it entirely — describe "
    "only your child's own feeling, never who it was about or where. Do not invent anything "
    "beyond the information provided. If the child was quiet, say so kindly. Under ~160 words."
)


def render_transcript(transcript: list[dict[str, Any]]) -> str:
    return "\n".join(f"{e.get('name', e.get('role', '?'))}: {e.get('text', '')}" for e in transcript)


class NoteTaker:
    def __init__(self) -> None:
        self._client = self._make_client()

    @staticmethod
    def _make_client() -> Any | None:
        if not os.getenv("ANTHROPIC_API_KEY"):
            return None
        try:
            import anthropic
        except ImportError:  # pragma: no cover - anthropic optional at runtime
            return None
        return anthropic.AsyncAnthropic()

    @property
    def enabled(self) -> bool:
        return self._client is not None

    async def update(self, transcript: list[dict[str, Any]]) -> str:
        """Refresh the live notes from the transcript so far."""
        return await self._note(transcript, _SYSTEM_LIVE, max_tokens=700)

    async def summary(self, transcript: list[dict[str, Any]]) -> str:
        """Write the end-of-session note."""
        return await self._note(transcript, _SYSTEM_FINAL, max_tokens=1000)

    async def parent_summary_for_kid(
        self, *, kid_name: str, own_lines: list[str], activities: list[str], participation: str
    ) -> str:
        """Warm, parent-facing recap for ONE child. Private by construction: only this
        child's own words, generic activity labels, and a participation note are ever sent
        to the model — no other child's text reaches it. Fails open to empty."""
        if self._client is None:
            return ""
        own = "\n".join(line for line in own_lines if line.strip()).strip() or "(did not say much out loud today)"
        acts = ", ".join(a for a in activities if a.strip()) or "a group feelings check-in"
        user = (
            f"Child's first name: {kid_name}\n"
            f"What the group did (activities only — no other children are named): {acts}\n"
            f"How {kid_name} took part: {participation}\n\n"
            f"{kid_name}'s own words and feelings during the session:\n{own}"
        )
        try:
            resp = await self._client.with_options(timeout=_TIMEOUT_SECONDS).messages.create(
                model=MODEL,
                max_tokens=500,
                system=[{"type": "text", "text": _SYSTEM_PARENT_KID, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}],
            )
        except Exception:
            return ""
        return "".join(
            block.text for block in resp.content if getattr(block, "type", None) == "text"
        ).strip()

    async def _note(self, transcript: list[dict[str, Any]], system: str, *, max_tokens: int) -> str:
        body = render_transcript(transcript).strip()
        if not body:
            return ""
        if self._client is None:
            return f"_Transcript (set ANTHROPIC_API_KEY for AI notes):_\n\n{body}"
        try:
            resp = await self._client.with_options(timeout=_TIMEOUT_SECONDS).messages.create(
                model=MODEL,
                max_tokens=max_tokens,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": f"Transcript so far:\n\n{body}"}],
            )
        except Exception:
            return f"_(AI notes unavailable — showing transcript)_\n\n{body}"
        text = "".join(
            block.text for block in resp.content if getattr(block, "type", None) == "text"
        ).strip()
        return text or body
