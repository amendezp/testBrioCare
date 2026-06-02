"""LLM phraser: rewrite the engine's literal facilitator lines as warm, natural,
kid-friendly speech via Claude.

Design constraints (per the project's claude-api skill):
- Official ``anthropic`` async SDK.
- ``claude-haiku-4-5`` for low latency (an explicit user override of the usual
  opus default — phrasing one short line must feel instant).
- A *frozen* system prompt marked ``cache_control: ephemeral`` so the cache prefix
  is stable. (It is below haiku's 4096-token minimum cacheable size, so caching
  won't actually engage yet; the structure is correct if the prompt grows.)
- **Graceful degradation**: if there is no ``ANTHROPIC_API_KEY``, the ``anthropic``
  package is missing, or the call errors/times out, return the literal text. The
  demo must run with zero configuration.

Only the three *spoken* action types carry text worth phrasing
(``SayPrompt`` / ``InviteParticipant`` / ``AcknowledgeSpeaker``); everything else
is a silent control signal and never reaches the phraser.
"""

from __future__ import annotations

import os
from typing import Any

MODEL = "claude-haiku-4-5"
_TIMEOUT_SECONDS = 2.0
_MAX_HISTORY = 6

_SYSTEM = (
    "You are the warm, gentle spoken voice of an AI facilitator helping a human "
    "clinician run a one-on-one social-emotional check-in with a child aged 7-11. "
    "You will be given the facilitator's literal scripted line plus a little context. "
    "Rewrite it as ONE short, natural, kid-friendly spoken sentence. Stay encouraging, "
    "concrete and calm. Never invent new questions or topics the script did not ask. "
    "Do not add emojis, stage directions, or quotation marks. Return only the spoken line."
)


def _format_history(recent_history: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for entry in recent_history[-_MAX_HISTORY:]:
        payload = entry.get("payload", {})
        kind = entry.get("kind", "")
        if kind == "participant_spoke":
            lines.append(f"Child said: {payload.get('text', '')}")
        elif kind in {"say_prompt", "invite_participant", "acknowledge_speaker"}:
            text = payload.get("text")
            if text:
                lines.append(f"Facilitator said: {text}")
    return "\n".join(lines)


class Phraser:
    """Async, fail-open wrapper around the Anthropic Messages API."""

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

    async def phrase(
        self,
        action: Any,
        *,
        facilitator_notes: str | None = None,
        recent_history: list[dict[str, Any]] | None = None,
    ) -> str:
        """Return a natural spoken line for ``action``, or its literal text on any failure."""
        raw = (getattr(action, "text", None) or "").strip()
        if self._client is None or not raw:
            return raw

        notes = (facilitator_notes or "").strip()
        history = _format_history(recent_history or [])
        user_parts = [f"Facilitator's literal line: {raw}"]
        if notes:
            user_parts.append(f"Facilitator notes (guidance, do not read aloud): {notes}")
        if history:
            user_parts.append(f"Recent exchange:\n{history}")
        user = "\n\n".join(user_parts)

        try:
            resp = await self._client.with_options(timeout=_TIMEOUT_SECONDS).messages.create(
                model=MODEL,
                max_tokens=120,
                system=[
                    {"type": "text", "text": _SYSTEM, "cache_control": {"type": "ephemeral"}}
                ],
                messages=[{"role": "user", "content": user}],
            )
        except Exception:
            return raw

        text = "".join(
            block.text for block in resp.content if getattr(block, "type", None) == "text"
        ).strip()
        return text or raw
