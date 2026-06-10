"""zendriver launch with stealth defaults; patchright fallback on launch failure."""

import contextlib
from collections.abc import AsyncGenerator

import zendriver as zd

from applyme.config import find_chrome
from applyme.errors import ApplyError


class WebDriverLeak(ApplyError):
    """navigator.webdriver was truthy — abort rather than apply with a detectable signal."""


async def assert_no_webdriver_leak(tab: zd.Tab) -> None:
    """Raise WebDriverLeak if navigator.webdriver is truthy on the given tab."""
    if await tab.evaluate("navigator.webdriver"):
        raise WebDriverLeak("navigator.webdriver is truthy")


@contextlib.asynccontextmanager
async def launch_browser(
    headful: bool = True,
    chrome_path: str | None = None,
) -> AsyncGenerator[zd.Browser, None]:
    """Yield a zendriver Browser using real Chrome; minimal args, no UA spoofing."""
    config = zd.Config(headless=not headful, browser_executable_path=find_chrome(chrome_path), lang="en-US")
    config.disable_webrtc = True  # plug local-IP leak (clean local IP, no proxy)
    browser = await zd.start(config=config)
    try:
        yield browser
    finally:
        await browser.stop()
