"""Patchright (stealth Playwright) engine.

Patchright is a drop-in Playwright fork with anti-detection patches. Unlike raw-CDP drivers, it
tracks the JS execution-context lifecycle and AUTO-WAITS through navigations and re-renders — so its
`fill()` / `check()` / `select_option()` survive Lever's parseResume re-render (which hangs zendriver).
We point it at the system Chrome/Chromium via `executable_path` (no separate browser download).
"""

import contextlib
from collections.abc import AsyncGenerator

from patchright.async_api import Page, async_playwright

from applyme.config import find_chrome


@contextlib.asynccontextmanager
async def launch_playwright(
    headful: bool = True,
    chrome_path: str | None = None,
    no_sandbox: bool = False,
) -> AsyncGenerator[Page, None]:
    """Yield a stealth Playwright Page driving the system Chrome.

    Chrome's sandbox cannot initialise as root / in many containers; `no_sandbox` (env
    JOOBLE_CHROME_NO_SANDBOX) disables it there. We do not spoof the UA — the real browser's
    fingerprint is the stealthiest, and patchright already hides the automation signals.
    """
    args = ["--disable-blink-features=AutomationControlled"]
    if no_sandbox:
        args += ["--no-sandbox", "--disable-setuid-sandbox"]
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=not headful,
            executable_path=find_chrome(chrome_path),
            args=args,
        )
        try:
            page = await browser.new_page()
            yield page
        finally:
            await browser.close()


async def assert_no_webdriver_leak(page: Page) -> None:
    """Raise if navigator.webdriver is truthy — abort rather than apply with a detectable signal."""
    if await page.evaluate("navigator.webdriver"):
        from applyme.errors import WebDriverLeak

        raise WebDriverLeak("navigator.webdriver is truthy")
