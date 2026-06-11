"""Patchright (stealth Playwright) engine — persistent context, real Chrome, coherent fingerprint.

Patchright is a drop-in Playwright fork with anti-detection patches. Unlike raw-CDP drivers, it tracks
the JS execution-context lifecycle and AUTO-WAITS through navigations and re-renders — so its `fill()` /
`check()` / `select_option()` survive Lever's parseResume re-render (which hangs zendriver).

The launch follows patchright's documented "completely undetected" config, because hCaptcha's invisible
Enterprise mode grades the session at a passive Stage-1 BEFORE any challenge renders (docs/REPORT.md §4):
- **Persistent context** (not a fresh `new_page()`): carries the Cloudflare `__cf_bm`/`cf_clearance`
  cookies across the sequential applies so vacancies 2-5 ride an already-warmed session; a fresh
  ephemeral context is a zero-history "new device" tell.
- **No forced viewport** (`no_viewport=True` when headful): the real OS window keeps `window.inner/outer`
  + `screen.*` + `devicePixelRatio` mutually coherent (a forced viewport desyncs them — a CreepJS tell).
- **Real system Chrome** via `executable_path` (find_chrome → Google Chrome on macOS, not Chromium) and
  **no UA/header spoofing** (a hand-set UA/Accept-Language desyncs the client-hints — patchright forbids it).
- **Coherence pinning** of locale/timezone to the egress-IP geo, and a WebRTC host-IP-leak block.
"""

import contextlib
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import cast

from patchright.async_api import Page, ProxySettings, async_playwright

from applyme.config import find_chrome


def _profile_dir(user_data_dir: str | None) -> Path:
    """Resolve the persistent Chrome profile dir (dedicated, NOT the live default profile)."""
    path = Path(user_data_dir) if user_data_dir else Path.home() / ".applyme" / "chrome-profile"
    path.mkdir(parents=True, exist_ok=True)
    return path


@contextlib.asynccontextmanager
async def launch_playwright(
    headful: bool = True,
    chrome_path: str | None = None,
    no_sandbox: bool = False,
    user_data_dir: str | None = None,
    proxy: dict[str, str] | None = None,
    locale: str = "en-US",
    timezone_id: str | None = None,
) -> AsyncGenerator[Page, None]:
    """Yield a stealth Playwright Page driving a PERSISTENT real-Chrome profile.

    Chrome's sandbox cannot initialise as root / in many containers; `no_sandbox` (env
    JOOBLE_CHROME_NO_SANDBOX) disables it there. We do not spoof the UA — the real browser's fingerprint
    is the stealthiest, and patchright already hides the automation signals. `no_viewport` is applied only
    when headful (the silent-pass target); headless keeps a default viewport for stable evidence capture.
    """
    args = [
        "--disable-blink-features=AutomationControlled",
        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    ]
    if no_sandbox:
        args += ["--no-sandbox", "--disable-setuid-sandbox"]
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=str(_profile_dir(user_data_dir)),
            executable_path=find_chrome(chrome_path),
            headless=not headful,
            no_viewport=True if headful else None,
            args=args,
            locale=locale,
            timezone_id=timezone_id,
            proxy=cast("ProxySettings | None", proxy),  # {server,username?,password?}; None ⇒ direct connection
        )
        try:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            yield page
        finally:
            await ctx.close()


async def assert_no_webdriver_leak(page: Page) -> None:
    """Raise if navigator.webdriver is truthy — abort rather than apply with a detectable signal."""
    if await page.evaluate("navigator.webdriver"):
        from applyme.errors import WebDriverLeak

        raise WebDriverLeak("navigator.webdriver is truthy")
