import pytest

from applyme.browser.engine import launch_browser
from applyme.browser.warmup import warm_session

pytestmark = pytest.mark.integration


async def test_warm_session_lands_on_company_then_posting(browser_launch_kwargs):
    async with launch_browser(**browser_launch_kwargs) as browser:
        tab = await warm_session(
            browser,
            company="leverdemo",
            apply_url="https://jobs.lever.co/leverdemo/<id>/apply",
            seed=1,
        )
        assert "leverdemo" in tab.url
