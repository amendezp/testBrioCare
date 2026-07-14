"""Per-child parent-summary privacy controls — regression tests for the leak vectors
surfaced by the adversarial red-team (surface-form mismatch, non-roster names, sibling
names/places, duplicate first names, letter-elongation), plus the fail-closed validator."""

from __future__ import annotations

from server import privacy


def test_redact_catches_roster_names_and_inflections() -> None:
    others = ["Leo"]
    assert "Leo" not in privacy.redact_roster_names("played with Leo today", others)
    assert "Leo" not in privacy.redact_roster_names("that's Leo's ball", others)  # possessive
    assert "a friend" in privacy.redact_roster_names("kept calling Leooo", others)  # elongation
    assert "Leo" not in privacy.redact_roster_names("the Leos at the table", others).replace("a friend", "")


def test_redact_catches_surface_form_extension() -> None:
    # roster token "Soph"; child says "Sophie" — the prefix match must still catch it
    assert "Soph" not in privacy.redact_roster_names("sat with Sophie", ["Soph"])


def test_redact_longest_first_avoids_prefix_preemption() -> None:
    out = privacy.redact_roster_names("Leon and Leo were here", ["Leo", "Leon"])
    assert "Leon" not in out and "Leo" not in out.replace("a friend", "")


def test_residual_proper_noun_detects_names_and_places() -> None:
    assert privacy.has_residual_proper_noun("your child mentioned a friend named Tyler.")  # non-roster name
    assert privacy.has_residual_proper_noun("a playdate on Maple Street.")  # place
    assert privacy.has_residual_proper_noun("worried about a brother named Theo.")  # sibling
    # clean, generic summaries must NOT trip the detector
    assert not privacy.has_residual_proper_noun(
        "Today, your child took part warmly. She shared that she felt happy with a friend."
    )
    assert not privacy.has_residual_proper_noun("Your child was quiet but checked in with the group.")


def test_residual_allows_the_childs_own_name() -> None:
    assert not privacy.has_residual_proper_noun("Maya felt proud today.", keep="Maya")


def test_sanitize_fails_closed_on_unredactable_identifier() -> None:
    # a non-roster name the roster filter can't know about -> drop the whole summary
    assert privacy.sanitize_summary("Your child played with Tyler.", others=["Leo"], keep="Maya") == ""
    # but a clean summary survives
    assert privacy.sanitize_summary(
        "Your child felt happy and joined a friend.", others=["Leo"], keep="Maya"
    ).startswith("Your child")


def test_sanitize_allows_activity_title_tokens() -> None:
    # Found in the live E2E: the model echoes activity titles ("Warm-up", "Role-play
    # corner") mid-sentence; those are script constants and must NOT drop the summary.
    allow = privacy.title_tokens(["Warm-up", "Role-play corner", "Feeling naming"])
    text = "Your child joined the Warm-up and loved the Role-play corner."
    assert privacy.sanitize_summary(text, others=["Leo"], keep="Maya", allow=allow) == text
    # a real name still fails closed even with the allowlist present
    assert privacy.sanitize_summary(
        "Your child sat with Tyler during the Warm-up.", others=["Leo"], keep="Maya", allow=allow
    ) == ""


def test_scrub_own_lines_removes_peers_before_generation() -> None:
    out = privacy.scrub_own_lines(["i sat with Leo", "i feel happy"], others=["Leo"])
    assert out == ["i sat with a friend", "i feel happy"]


def test_dedupe_names_disambiguates_duplicates() -> None:
    out = privacy.dedupe_names({"kid1": "Sam", "kid2": "Sam", "kid3": "Mia"})
    assert out == {"kid1": "Sam", "kid2": "Sam 2", "kid3": "Mia"}
