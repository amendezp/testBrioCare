"""Guardian (phase 1): the crisis-keyword scanner.

This is a safety net, not a clinical instrument — the tests pin its *behavior*
(it flags concerning phrases, it returns the category/label/match a clinician needs,
and it stays quiet on ordinary talk), never a claim of clinical completeness.
"""

from __future__ import annotations

import pytest
from server import safety


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("sometimes I want to die", "self_harm"),
        ("I want to kill myself", "self_harm"),
        ("I've been cutting myself", "self_harm"),
        ("my mom hits me when she's mad", "abuse"),
        ("I'm scared to go home", "abuse"),
        ("nobody likes me and I hate myself", "acute_distress"),
        ("I want to run away", "acute_distress"),
    ],
)
def test_flags_concerning_phrases(text: str, category: str) -> None:
    hit = safety.scan(text)
    assert hit is not None
    assert hit.category == category
    assert hit.matched  # the exact phrase, for the clinician's context
    assert hit.label


@pytest.mark.parametrize(
    "text",
    [
        "",
        "I had a great day at school",
        "my favorite animal is a fox",
        "she is so cute",  # must NOT match "cut" — word boundaries
        "we cooked dinner together",
        "I feel happy and calm today",
    ],
)
def test_quiet_on_ordinary_talk(text: str) -> None:
    assert safety.scan(text) is None


def test_matching_is_case_insensitive() -> None:
    hit = safety.scan("I WANT TO DIE")
    assert hit is not None
    assert hit.category == "self_harm"


def test_returns_first_hit_only() -> None:
    # Two categories present; scanner returns a single, deterministic hit (self_harm first).
    hit = safety.scan("I want to die and I want to run away")
    assert hit is not None
    assert hit.category == "self_harm"
