import pytest

from applyme.browser.engine import assert_no_webdriver_leak, launch_browser

pytestmark = pytest.mark.integration


async def test_launch_and_no_webdriver_leak():
    async with launch_browser(headful=True) as browser:
        tab = await browser.get("https://example.com")
        await assert_no_webdriver_leak(tab)  # raises if navigator.webdriver is truthy
        assert (await tab.evaluate("navigator.userAgent")) and "Headless" not in await tab.evaluate(
            "navigator.userAgent"
        )
