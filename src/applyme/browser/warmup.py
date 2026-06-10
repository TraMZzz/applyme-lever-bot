"""Warm the Cloudflare session: company page → dwell/scroll → posting, before /apply."""

import asyncio
import random

import zendriver as zd

from applyme.browser.human import sample_delay


async def warm_session(browser: zd.Browser, company: str, apply_url: str, seed: int) -> zd.Tab:
    """Navigate company jobs page, dwell + scroll, then navigate to the posting URL."""
    rng = random.Random(seed)
    tab: zd.Tab = await browser.get(f"https://jobs.lever.co/{company}")
    await asyncio.sleep(sample_delay("read_page", rng))
    for _ in range(rng.randint(2, 4)):  # event-driven scroll
        await tab.scroll_down(rng.randint(200, 600))
        await asyncio.sleep(sample_delay("field_think", rng))
    posting_url = apply_url.removesuffix("/apply")
    await tab.get(posting_url)
    await asyncio.sleep(sample_delay("read_page", rng))
    return tab
