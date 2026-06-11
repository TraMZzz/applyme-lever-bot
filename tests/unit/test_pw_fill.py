"""URL-field → profile-link matching and the card-option fuzzy fallback."""

from applyme.lever.pw_fill import _closest_option, _link_for_label

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


_OPTS = [
    "I’ve occasionally worked with visuals, but it wasn’t a key part of my role.",
    "I’ve led creative direction or managed design initiatives tied to content strategy.",
]


def test_closest_option_bridges_minor_encoding_diff():
    # Same option, straight quotes + extra whitespace -> high similarity -> matched to the real value.
    answer = "I've occasionally worked with visuals,  but it wasn't a key part of my role."
    assert _closest_option(answer, _OPTS) == _OPTS[0]


def test_closest_option_rejects_far_answer():
    # A salary number is nowhere near Yes/No -> stays unmatched (field fail-closes, no wrong tick).
    assert _closest_option("140000", ["Yes", "No"]) is None
    assert _closest_option("", _OPTS) is None
