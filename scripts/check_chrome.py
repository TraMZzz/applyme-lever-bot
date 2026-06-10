#!/usr/bin/env python3
"""Minimal Chrome smoke — run on a desktop session with Chrome installed:

    uv run python scripts/check_chrome.py

Needs nothing but Chrome (no data/, no API keys). It launches the real browser, loads a Lever
apply page through Cloudflare, asserts no `navigator.webdriver` leak, and parses the form — i.e.
verifies the engine actually drives Chrome end-to-end. If this prints OK, `applyme run` will work.

If you hit "Failed to connect to browser": you're likely in a headless/CI/container context
(no display). Set JOOBLE_HEADFUL=false won't help connect there; run on a real desktop session,
or for containers add a no-sandbox launch (see README "Inputs & email" / engine.py).
"""

import asyncio

from applyme.browser.engine import assert_no_webdriver_leak, launch_browser
from applyme.config import chrome_version, find_chrome
from applyme.lever.form import parse_form_html

LEVER = "https://jobs.lever.co/leverdemo/33538a2f-d27d-4a96-8f05-fa4b0e4d940e/apply"


async def main() -> None:
    print(f"Chrome: {chrome_version(find_chrome())}")
    async with launch_browser(headful=True) as browser:
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
