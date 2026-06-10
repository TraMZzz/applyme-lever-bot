#!/usr/bin/env python3
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Minimal Chrome smoke — run on a session with Chrome installed:

    uv run python scripts/check_chrome.py

Needs nothing but Chrome (no data/, no API keys). Launches the real browser, loads a Lever apply
page through Cloudflare, asserts no `navigator.webdriver` leak, and parses the form — i.e. verifies
the engine actually drives Chrome end-to-end. If this prints OK, `applyme run` will work.

Reads the same env as the bot:
  JOOBLE_HEADFUL=false          → headless (use in a non-GUI / SSH / CI / container session)
  JOOBLE_CHROME_NO_SANDBOX=true → disable Chrome's sandbox (root/containers); the engine also
                                  auto-retries without the sandbox on a connect failure.
  JOOBLE_CHROME_PATH=/path      → override Chrome location.
"""

import asyncio

from applyme.browser.engine import assert_no_webdriver_leak, launch_browser
from applyme.config import Settings, chrome_version, find_chrome
from applyme.lever.form import parse_form_html

LEVER = "https://jobs.lever.co/leverdemo/33538a2f-d27d-4a96-8f05-fa4b0e4d940e/apply"


async def main() -> None:
    s = Settings()
    print(f"Chrome path : {find_chrome(s.chrome_path)}")
    print(f"Chrome ver  : {chrome_version(find_chrome(s.chrome_path))}")
    print(f"mode        : headful={s.headful} no_sandbox={s.chrome_no_sandbox}")
    async with launch_browser(
        headful=s.headful, chrome_path=s.chrome_path, no_sandbox=s.chrome_no_sandbox
    ) as browser:
        tab = await browser.get(LEVER)
        await asyncio.sleep(3)
        await assert_no_webdriver_leak(tab)
        spec = parse_form_html(await tab.get_content(), posting_url=LEVER)
        print(
            f"OK | url={tab.url} | navigator.webdriver=false | "
            f"sitekey={spec.sitekey or 'NONE'} | standard_fields={len(spec.standard_fields)} | cards={len(spec.cards)}"
        )


if __name__ == "__main__":
    asyncio.run(main())
