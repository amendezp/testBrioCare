"""The fail-silent end-of-session JSON dump."""

from __future__ import annotations

import json

from server import dump


def test_dump_session_writes_json(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BRIOCARE_DUMP_DIR", str(tmp_path))
    path = dump.dump_session(
        code="g1",
        started_at="2026-06-16T10:00:00",
        roster={"kid1": "Maya"},
        ratings={"feelings_checkin": {"kid1": 4}, "feelings_checkout": {"kid1": 2}},
        transcript=[{"name": "Maya", "text": "hi", "kind": "rating"}],
        final_notes="## Summary\nGood session.",
        parent_summaries={"kid1": {"name": "Maya", "summary": "Today your child took part."}},
    )
    assert path is not None and path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["ratings"]["feelings_checkin"]["kid1"] == 4
    assert data["ratings"]["feelings_checkout"]["kid1"] == 2
    assert data["parent_summaries"]["kid1"]["summary"].startswith("Today")


def test_dump_session_is_fail_silent(tmp_path, monkeypatch) -> None:
    # Point the dump dir *under an existing file* so mkdir fails; the dump must swallow it.
    a_file = tmp_path / "not_a_dir"
    a_file.write_text("x", encoding="utf-8")
    monkeypatch.setenv("BRIOCARE_DUMP_DIR", str(a_file / "sub"))
    assert dump.dump_session(code="x", started_at=None, roster={}, ratings={}, transcript=[]) is None
