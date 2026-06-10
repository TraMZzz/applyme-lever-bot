"""zendriver launch with stealth defaults (real Chrome, genuine fingerprint)."""

import contextlib
import os
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


def _build_config(headful: bool, chrome_path: str | None, sandbox: bool) -> zd.Config:
    """Build a zendriver Config. Language is left to the system Chrome locale: passing
    Config(lang=...) makes zendriver emit a --lang arg it then rejects at start()."""
    config = zd.Config(
        headless=not headful,
        browser_executable_path=find_chrome(chrome_path),
        sandbox=sandbox,
    )
    config.disable_webrtc = True  # plug local-IP leak (clean local IP, no proxy); default True, set explicitly
    return config


@contextlib.asynccontextmanager
async def launch_browser(
    headful: bool = True,
    chrome_path: str | None = None,
    no_sandbox: bool = False,
) -> AsyncGenerator[zd.Browser, None]:
    """Yield a zendriver Browser using real Chrome; minimal args, no UA spoofing.

    Chrome's own sandbox cannot initialise as root or in many containers/CI. We keep it ON for a
    normal desktop user (it's the stealthier default and avoids the detectable --no-sandbox arg),
    but disable it when running as root, when `no_sandbox` is requested (env JOOBLE_CHROME_NO_SANDBOX),
    OR automatically on a first connect failure — which is exactly what zendriver suggests for the
    "Failed to connect to browser" case.
    """
    running_as_root = hasattr(os, "geteuid") and os.geteuid() == 0
    use_sandbox = not (running_as_root or no_sandbox)
    try:
        browser = await zd.start(config=_build_config(headful, chrome_path, sandbox=use_sandbox))
    except Exception:  # noqa: BLE001 — auto-fallback: retry once without Chrome's sandbox
        if not use_sandbox:
            raise
        browser = await zd.start(config=_build_config(headful, chrome_path, sandbox=False))
    try:
        yield browser
    finally:
        await browser.stop()
