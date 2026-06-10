from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture():
    return lambda name: (FIXTURES / name).read_text()


# ---------------------------------------------------------------------------
# Fixtures for the T21 integration test (fake page + profile)
# ---------------------------------------------------------------------------


class _FakePage:
    """Minimal duck-typed page object for apply_to_vacancy_with_page tests.

    Provides get_content() and exposes final_url / http_status as attributes.
    Browser-specific methods (fill_form, get_captcha_token, submit, save_screenshot)
    are intentionally absent so the app falls through to the attribute path.
    """

    def __init__(self, html: str, final_url: str, http_status: int) -> None:
        self._html = html
        self.final_url = final_url
        self.http_status = http_status

    async def get_content(self) -> str:
        return self._html


@pytest.fixture
def fake_page_factory():
    """Return a factory that builds a _FakePage from a fixture name + submission metadata."""

    def _make(apply_html_fixture: str, final_url: str, status: int) -> _FakePage:
        html = (FIXTURES / apply_html_fixture).read_text()
        return _FakePage(html=html, final_url=final_url, http_status=status)

    return _make


@pytest.fixture
def profile_fixture():
    """A minimal CandidateProfile suitable for unit/integration tests."""
    from pathlib import Path as _Path

    from applyme.models import CandidateProfile

    return CandidateProfile(
        full_name="Ethan Calder",
        email="ethan@applyme.site",
        phone="9175552244",
        location="New York, NY, United States",
        city="New York",
        state="NY",
        country="United States",
        work_authorized=True,
        requires_sponsorship=False,
        willing_to_relocate=False,
        expected_salary=140000,
        resume_path=_Path("data/resume.pdf"),
    )
