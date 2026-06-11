"""URL-field → profile-link matching for the `urls[<label>]` fill."""

from applyme.lever.pw_fill import _link_for_label

_LINKS = {"LinkedIn": "https://linkedin.com/in/x", "GitHub": "https://github.com/x"}


def test_link_matches_exact_label():
    assert _link_for_label("LinkedIn", _LINKS) == "https://linkedin.com/in/x"
    assert _link_for_label("GitHub", _LINKS) == "https://github.com/x"


def test_link_matches_when_label_wraps_the_key():
    # padsplit names the field `urls[Please provide your LinkedIn]` — the key is a substring of the label.
    assert _link_for_label("Please provide your LinkedIn", _LINKS) == "https://linkedin.com/in/x"


def test_link_is_case_insensitive():
    assert _link_for_label("linkedin url", _LINKS) == "https://linkedin.com/in/x"


def test_no_match_returns_none():
    assert _link_for_label("Portfolio", _LINKS) is None
    assert _link_for_label("LinkedIn", {}) is None
    assert _link_for_label("Website", {"Twitter": "https://x.com/y"}) is None
