"""The Phraser must fail open: never raise, always fall back to literal text."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from server.phraser import Phraser

from briocare.runtime.actions import PromptSource, SayPrompt


def _say(text: str = "Tell me how you are feeling.") -> SayPrompt:
    return SayPrompt(at=0.0, source=PromptSource.PHASE_OPENING, text=text)


def test_disabled_without_api_key(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    phraser = Phraser()
    assert phraser.enabled is False
    out = asyncio.run(phraser.phrase(_say("hello there")))
    assert out == "hello there"


def test_falls_back_when_client_raises() -> None:
    phraser = Phraser()

    class _Boom:
        def with_options(self, **_kw):
            return self

        @property
        def messages(self):
            return self

        async def create(self, **_kw):
            raise RuntimeError("network down")

    phraser._client = _Boom()
    out = asyncio.run(phraser.phrase(_say("literal line")))
    assert out == "literal line"


def test_uses_model_text_when_available() -> None:
    phraser = Phraser()

    class _OK:
        def with_options(self, **_kw):
            return self

        @property
        def messages(self):
            return self

        async def create(self, **_kw):
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="Hey, how are you feeling?")])

    phraser._client = _OK()
    out = asyncio.run(phraser.phrase(_say()))
    assert out == "Hey, how are you feeling?"


def test_empty_text_returns_empty() -> None:
    phraser = Phraser()
    out = asyncio.run(phraser.phrase(SayPrompt(at=0.0, source=PromptSource.INTRO, text="")))
    assert out == ""
