from __future__ import annotations

import pytest
from pydantic import ValidationError

from briocare.scripts.schema import (
    AdvanceWhen,
    ExerciseScript,
    PacingRule,
    Phase,
    Prompt,
    TurnOrder,
    TurnPolicy,
)
from tests.conftest import make_phase, make_script


def test_prompt_accepts_plain_string_and_strips_whitespace() -> None:
    p = Prompt.model_validate("  hello\n  world  ")
    assert p.text == "hello world"
    assert p.addressed_to == "group"


def test_valid_script_round_trips() -> None:
    script = make_script(make_phase("p1", order=TurnOrder.ROUND_ROBIN))
    dumped = script.model_dump()
    assert ExerciseScript.model_validate(dumped) == script


def test_per_turn_hard_must_be_ge_soft() -> None:
    with pytest.raises(ValidationError, match="per_turn_hard_seconds"):
        TurnPolicy(per_turn_seconds=60, per_turn_hard_seconds=30)


def test_duplicate_phase_ids_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate phase ids"):
        make_script(make_phase("dup"), make_phase("dup"))


def test_advance_when_timer_requires_max_phase_seconds() -> None:
    with pytest.raises(ValidationError, match="requires max_phase_seconds"):
        PacingRule(advance_when=AdvanceWhen.TIMER)


def test_wrapup_warning_must_be_less_than_max() -> None:
    with pytest.raises(ValidationError, match="wrapup_warning_seconds must be < max"):
        PacingRule(max_phase_seconds=30, wrapup_warning_seconds=30)


def test_unknown_key_rejected() -> None:
    with pytest.raises(ValidationError):
        Phase.model_validate(
            {"id": "x", "title": "X", "opening_prompt": "hi", "surprise": True}
        )


def test_at_least_one_phase_required() -> None:
    with pytest.raises(ValidationError):
        ExerciseScript(id="x", title="X", phases=[])
