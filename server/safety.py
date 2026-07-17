"""Guardian (phase 1): a real-time crisis-keyword scanner for child utterances.

This is a **safety net, not a clinical instrument**. It exists so a concerning
disclosure — self-harm, abuse, acute distress — cannot pass by unseen: any match
raises a high-priority, human-facing alert to the supervising clinician immediately.
It deliberately errs toward surfacing (false positives are fine; a missed disclosure
is not). It never blocks, never diagnoses, and never decides anything on its own.

The keyword lists are a starting point and must be reviewed and expanded with a
licensed clinician before any pilot with real children.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# (category, human label, phrases). Phrases are matched case-insensitively; single
# words are anchored to word boundaries so "cut" matches "cut myself" but not "cute".
_PATTERNS: list[tuple[str, str, list[str]]] = [
    (
        "self_harm",
        "possible self-harm / suicidal talk",
        [
            "kill myself", "kill me", "want to die", "wanna die", "end my life", "end it all",
            "hurt myself", "hurting myself", "cut myself", "cutting myself", "self harm",
            "no reason to live", "don't want to be here", "dont want to be here",
            "want to disappear", "better off dead", "suicide", "suicidal",
        ],
    ),
    (
        "abuse",
        "possible abuse / someone hurting them",
        [
            "hits me", "hit me", "hurts me", "hurt me", "beats me", "beat me",
            "touched me", "touches me", "makes me do", "not allowed to tell",
            "scared to go home", "afraid to go home", "scared of my", "afraid of my",
        ],
    ),
    (
        "acute_distress",
        "acute distress",
        [
            "everyone hates me", "nobody likes me", "no one likes me", "i hate myself",
            "i'm worthless", "im worthless", "want to run away", "can't do this anymore",
            "cant do this anymore", "scared all the time",
        ],
    ),
]

_COMPILED: list[tuple[str, str, re.Pattern[str]]] = [
    (
        cat,
        label,
        re.compile("|".join(rf"\b{re.escape(p)}\b" for p in phrases), re.IGNORECASE),
    )
    for cat, label, phrases in _PATTERNS
]


@dataclass(frozen=True)
class RiskHit:
    category: str
    label: str
    matched: str  # the exact phrase that matched, for the clinician's context


def scan(text: str) -> RiskHit | None:
    """Return the first risk hit in ``text``, or None. Cheap, synchronous, no I/O."""
    if not text:
        return None
    for category, label, pattern in _COMPILED:
        m = pattern.search(text)
        if m:
            return RiskHit(category=category, label=label, matched=m.group(0))
    return None
