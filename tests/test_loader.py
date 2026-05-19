from __future__ import annotations

from pathlib import Path

import pytest

from briocare.scripts.loader import ScriptValidationError, load_script
from tests.conftest import FIXTURES, LIBRARY


def test_loads_library_script() -> None:
    script = load_script(LIBRARY / "feelings_checkin_circle.yaml")
    assert script.id == "feelings_checkin_circle"
    assert [p.id for p in script.phases] == ["model_and_warmup", "go_around", "reflect"]


def test_loads_fixture_checkin() -> None:
    script = load_script(FIXTURES / "checkin.yaml")
    assert script.phases[0].acknowledge_speakers is True
    assert script.intro_prompt is not None and script.intro_prompt.text.startswith("Welcome")


def test_missing_file_raises_clean_error(tmp_path: Path) -> None:
    with pytest.raises(ScriptValidationError, match="cannot read file"):
        load_script(tmp_path / "nope.yaml")


def test_yaml_parse_error(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("phases: [unclosed\n")
    with pytest.raises(ScriptValidationError, match="YAML parse error"):
        load_script(bad)


def test_non_mapping_top_level(tmp_path: Path) -> None:
    bad = tmp_path / "list.yaml"
    bad.write_text("- just\n- a\n- list\n")
    with pytest.raises(ScriptValidationError, match="top level must be a mapping"):
        load_script(bad)


def test_validation_error_lists_dotted_locations(tmp_path: Path) -> None:
    bad = tmp_path / "invalid.yaml"
    bad.write_text(
        "schema_version: 1\n"
        "id: x\n"
        "title: X\n"
        "phases:\n"
        "  - id: p1\n"
        "    title: P1\n"
        "    opening_prompt: hi\n"
        "    pacing:\n"
        "      max_phase_seconds: 30\n"
        "      wrapup_warning_seconds: 40\n"
    )
    with pytest.raises(ScriptValidationError) as exc:
        load_script(bad)
    msg = str(exc.value)
    assert "invalid.yaml" in msg
    assert "phases.0.pacing" in msg
    assert "wrapup_warning_seconds must be < max_phase_seconds" in msg
