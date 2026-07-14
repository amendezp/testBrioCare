"""Privacy helpers for per-child parent summaries.

A child's parent summary must never let another child be identified. The summary is
already built only from that child's OWN data, but a peer identifier can still ride in
on the child's own words ("I sat with Sophie", "her brother Theo"). These helpers are
the hard controls on that single channel:

- :func:`redact_roster_names` — replace any *other* roster child's name (and its
  prefix/inflected forms: "Leo" -> "Leo's", "Leooo", "Leos") with "a friend".
- :func:`has_residual_proper_noun` — detect any leftover name/place-shaped token.
- :func:`sanitize_summary` — redact roster names, then **fail closed**: if any
  proper-noun-shaped token still remains, drop the summary entirely rather than risk
  shipping an unredacted identifier.

Fail-closed is deliberate: for a children's product, withholding a summary (the
therapist still has the child's transcript) is far better than leaking a peer.
"""

from __future__ import annotations

import re

_GENERIC = "a friend"

# Capitalised words that are NOT identifying and are fine mid-sentence.
_SAFE_CAPS = {
    "A", "I", "I'm", "I've", "I'd", "I'll", "Im", "AI", "OK", "Okay", "TV",
    "He", "He's", "She", "She's", "They", "They're", "We", "We're", "It", "It's", "You", "Your",
    "His", "Her", "Hers", "Their", "Our", "Its",
    "That", "This", "These", "Those", "There", "Then", "Than", "When", "While", "What", "Who", "Why", "How",
    "Today", "Tomorrow", "Yesterday", "Now", "Later", "Maybe", "Yes", "No", "And", "But", "So",
    "Mom", "Mum", "Mommy", "Mummy", "Dad", "Daddy", "Grandma", "Grandpa", "Nana",
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December",
}

_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_TOKEN = re.compile(r"[A-Za-z][A-Za-z'’\-]*")  # noqa: RUF001 - curly apostrophe is intentional
_TITLECASE = re.compile(r"^[A-Z][a-z'’\-]+$")  # noqa: RUF001 - matches "Sophie","Maple","Leooo"


def redact_roster_names(text: str, others: list[str]) -> str:
    """Replace each OTHER child's name (and prefix/inflected forms) with 'a friend'.

    Longest names first so a name that is a prefix of another (e.g. 'Leo' vs 'Leon')
    can't pre-empt the longer one.
    """
    if not text:
        return text
    names = sorted({n.strip() for n in others if len(n.strip()) >= 2}, key=len, reverse=True)
    for nm in names:
        text = re.sub(rf"\b{re.escape(nm)}\w*", _GENERIC, text, flags=re.IGNORECASE)
    return text


def title_tokens(titles: list[str]) -> set[str]:
    """Tokens from known-safe script text (activity titles) that a summary may echo
    mid-sentence — e.g. 'Warm-up', 'Compliment', 'Role-play'. These are script
    constants, never children's names."""
    return {tok for t in titles for tok in _TOKEN.findall(t)}


def has_residual_proper_noun(text: str, *, keep: str = "", allow: set[str] | None = None) -> bool:
    """True if any title-cased token (other than the child's own name, a safe word, or
    an allowed script token) survives mid-sentence — i.e. a likely person or place name."""
    keep_l = keep.strip().lower()
    allowed = allow or set()
    for sentence in _SENTENCE.split(text.strip()):
        tokens = _TOKEN.findall(sentence)
        for i, tok in enumerate(tokens):
            if i == 0:
                continue  # sentence-initial capitalisation is expected
            if (
                _TITLECASE.match(tok)
                and tok not in _SAFE_CAPS
                and tok not in allowed
                and tok.lower() != keep_l
            ):
                return True
    return False


def scrub_own_lines(lines: list[str], *, others: list[str]) -> list[str]:
    """Pre-generation: strip other children's names from the child's own utterances
    before they ever reach the model (it can't echo what it never sees)."""
    return [redact_roster_names(line, others) for line in lines]


def sanitize_summary(text: str, *, others: list[str], keep: str, allow: set[str] | None = None) -> str:
    """Redact roster names, then fail closed: return '' if any name/place-shaped token
    still remains, so an unredacted identifier can never ship. ``allow`` whitelists
    known-safe script tokens (activity titles) to avoid false-positive drops."""
    if not text:
        return ""
    redacted = redact_roster_names(text, others)
    if has_residual_proper_noun(redacted, keep=keep, allow=allow):
        return ""  # fail closed — therapist falls back to the child's transcript
    return redacted


def dedupe_names(pid_to_name: dict[str, str]) -> dict[str, str]:
    """Make display names unique (two 'Sam's -> 'Sam', 'Sam 2') so name-based redaction
    is unambiguous across the roster."""
    seen: dict[str, int] = {}
    out: dict[str, str] = {}
    for pid, name in pid_to_name.items():
        key = name.strip().lower()
        seen[key] = seen.get(key, 0) + 1
        out[pid] = name if seen[key] == 1 else f"{name} {seen[key]}"
    return out
