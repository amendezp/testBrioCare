"""Daily room helper must fail open without a key and parse the REST response with one."""

from __future__ import annotations

import asyncio

import httpx
from server.daily import Daily, _room_name


def test_disabled_without_key(monkeypatch) -> None:
    monkeypatch.delenv("DAILY_API_KEY", raising=False)
    daily = Daily()
    assert daily.enabled is False
    assert asyncio.run(daily.get_or_create_room("demo")) is None


def test_creates_and_caches_room(monkeypatch) -> None:
    monkeypatch.setenv("DAILY_API_KEY", "test-key")

    class _Resp:
        def __init__(self, code: int, data: dict) -> None:
            self.status_code = code
            self._data = data

        def json(self) -> dict:
            return self._data

    calls = {"post": 0}

    class _Client:
        def __init__(self, *a, **k) -> None:
            pass

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *a) -> bool:
            return False

        async def post(self, url, headers=None, json=None) -> _Resp:
            calls["post"] += 1
            return _Resp(200, {"url": "https://x.daily.co/briocare-demo", "name": json["name"]})

        async def get(self, url, headers=None) -> _Resp:  # pragma: no cover - not hit on 200
            return _Resp(200, {"url": "https://x.daily.co/briocare-demo"})

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    daily = Daily()
    url = asyncio.run(daily.get_or_create_room("demo"))
    assert url == "https://x.daily.co/briocare-demo"
    # cached -> no second POST
    asyncio.run(daily.get_or_create_room("demo"))
    assert calls["post"] == 1


def test_returns_none_on_error(monkeypatch) -> None:
    monkeypatch.setenv("DAILY_API_KEY", "test-key")

    class _Client:
        def __init__(self, *a, **k) -> None:
            pass

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *a) -> bool:
            return False

        async def post(self, *a, **k):
            raise RuntimeError("boom")

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    assert asyncio.run(Daily().get_or_create_room("demo")) is None


def test_room_name_is_sanitised() -> None:
    assert _room_name("My Room!") == "briocare-My-Room"
    assert _room_name("") == "briocare-demo"
