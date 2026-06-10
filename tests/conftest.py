import re
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture():
    return lambda name: (FIXTURES / name).read_text()


# ---------------------------------------------------------------------------
# Fixtures for the apply-flow integration test (honest fake tab + profile)
# ---------------------------------------------------------------------------


class _FakeElement:
    """A fake element honouring the methods HumanActions / fill_form call on real elements."""

    def __init__(self, page: "_FakePage", name: str | None) -> None:
        self._page = page
        self._name = name

    async def scroll_into_view(self) -> None:
        return None

    async def get_position(self) -> "_FakePosition":
        return _FakePosition()

    async def send_keys(self, text: str) -> None:
        """Record typed characters against this element's name so evaluate() can read them back."""
        if self._name is not None:
            self._page.typed[self._name] = self._page.typed.get(self._name, "") + text

    async def send_file(self, path: str) -> None:
        """Record that the resume file was uploaded."""
        self._page.uploaded_file = path

    async def set_value(self, value: str) -> None:
        if self._name is not None:
            self._page.typed[self._name] = value


class _FakePosition:
    """A small in-bounds box so HumanActions can compute a jittered click point."""

    left = 10.0
    top = 10.0
    width = 80.0
    height = 20.0


class _NullResponseCtx:
    """Async-CM no-op standing in for tab.expect_response()."""

    async def __aenter__(self) -> "_NullResponseCtx":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


_NAME_RE = re.compile(r'\[name=\\?"?([a-zA-Z0-9_\[\]-]+)\\?"?\]')


class _FakePage:
    """Honest fake tab implementing the Page interface that the apply flow actually drives.

    It records typed/uploaded values (so the test can assert fill_form ran) and serves them back
    through evaluate(), so verify_overrides() reads the canonical values and passes.
    """

    def __init__(self, html: str, final_url: str, http_status: int) -> None:
        self._html = html
        self._final_url = final_url
        self.http_status = http_status
        self.typed: dict[str, str] = {}
        self.uploaded_file: str | None = None
        self.clicks: list[str] = []

    async def get_content(self) -> str:
        return self._html

    @property
    def url(self) -> str:
        return self._final_url

    async def select(self, selector: str) -> _FakeElement:
        if "input[type=file]" in selector:
            return _FakeElement(self, name="resume")
        m = _NAME_RE.search(selector)
        return _FakeElement(self, name=m.group(1) if m else None)

    async def find(self, text: str, best_match: bool = True) -> _FakeElement:
        return _FakeElement(self, name=None)

    def expect_response(self, pattern: str) -> _NullResponseCtx:
        return _NullResponseCtx()

    async def send(self, cmd: object) -> None:
        """Swallow CDP commands (mouse events) dispatched by HumanActions."""
        return None

    async def evaluate(self, expression: str) -> object:
        # verify_overrides readback: document.querySelector("[name=\"x\"]").value
        m = _NAME_RE.search(expression)
        if m and ".value" in expression and "querySelectorAll" not in expression:
            self.clicks.append(m.group(1))
            return self.typed.get(m.group(1), "")
        # _settle() readback: join the standard fields' current values.
        if "querySelectorAll" in expression:
            return "|".join(self.typed.get(n, "") for n in ("name", "email", "phone"))
        if "h-captcha-response" in expression:
            return ""  # no challenge in the fake
        if "iframe" in expression:
            return False
        if "userAgent" in expression:
            return "FakeUA/1.0"
        if "__lastStatus" in expression:
            return self.http_status
        return ""

    async def save_screenshot(self, path: str, full_page: bool = False) -> None:
        Path(path).write_bytes(b"")  # noqa: ASYNC240 — test fake, no real I/O concern


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
