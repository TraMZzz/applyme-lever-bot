"""Patchright engine integration test (needs a real Chrome). Run: pytest -m integration."""

import pytest

from applyme.browser.pw_engine import assert_no_webdriver_leak, launch_playwright
from applyme.config import Settings
from applyme.lever.form import parse_form_html

pytestmark = pytest.mark.integration

LEVER = "https://jobs.lever.co/leverdemo/33538a2f-d27d-4a96-8f05-fa4b0e4d940e/apply"


async def test_launch_no_webdriver_leak_and_parse():
    s = Settings()
    async with launch_playwright(headful=s.headful, chrome_path=s.chrome_path, no_sandbox=s.chrome_no_sandbox) as page:
        await page.goto(LEVER, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_selector('[name="resume"]', state="attached", timeout=20000)
        await assert_no_webdriver_leak(page)  # raises if navigator.webdriver is truthy
        spec = parse_form_html(await page.content(), posting_url=LEVER)
        assert spec.sitekey  # the invisible-hCaptcha sitekey was parsed off the live page
        assert spec.standard_fields  # name/email/phone/… present
