"""Note-taker must fail open to a readable transcript and use the model when available."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from server.notes import NoteTaker, render_transcript

_TX = [
    {"role": "therapist", "name": "Therapist", "text": "How was your week?"},
    {"role": "kid", "name": "Maya", "text": "kind of hard"},
]


def test_render_transcript() -> None:
    assert render_transcript(_TX) == "Therapist: How was your week?\nMaya: kind of hard"


def test_disabled_returns_transcript(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    nt = NoteTaker()
    assert nt.enabled is False
    out = asyncio.run(nt.update(_TX))
    assert "Maya: kind of hard" in out


def test_empty_transcript_returns_empty(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert asyncio.run(NoteTaker().update([])) == ""


def test_uses_model_when_available() -> None:
    nt = NoteTaker()

    class _OK:
        def with_options(self, **_kw):
            return self

        @property
        def messages(self):
            return self

        async def create(self, **_kw):
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="## Summary\nGood session.")])

    nt._client = _OK()
    out = asyncio.run(nt.summary(_TX))
    assert out == "## Summary\nGood session."


def test_falls_back_when_client_raises() -> None:
    nt = NoteTaker()

    class _Boom:
        def with_options(self, **_kw):
            return self

        @property
        def messages(self):
            return self

        async def create(self, **_kw):
            raise RuntimeError("network down")

    nt._client = _Boom()
    out = asyncio.run(nt.update(_TX))
    assert "Maya: kind of hard" in out
