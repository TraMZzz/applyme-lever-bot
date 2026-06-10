import pytest

from applyme.lever.verify import extract_lever_link, is_safe_lever_link


@pytest.mark.parametrize(
    "url,ok",
    [
        ("https://jobs.lever.co/a/b/thanks", True),
        ("https://hire.lever.co/confirm?t=1", True),
        ("http://jobs.lever.co/x", False),  # not https
        ("https://evil.com/lever.co", False),  # host not lever.co
        ("https://lever.co@evil.com/x", False),  # userinfo trick
    ],
)
def test_is_safe_lever_link(url: str, ok: bool) -> None:
    assert is_safe_lever_link(url) is ok


def test_extract_lever_link_ignores_tracking() -> None:
    body = 'Thanks! <a href="https://track.evil/x">click</a> or https://jobs.lever.co/a/b/thanks'
    assert extract_lever_link(body) == "https://jobs.lever.co/a/b/thanks"
