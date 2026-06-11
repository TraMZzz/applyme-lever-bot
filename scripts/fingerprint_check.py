#!/usr/bin/env python3
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Silent-pass readiness self-diagnostic — run headful on the machine that will apply:

    uv run python scripts/fingerprint_check.py

hCaptcha's invisible Enterprise mode blocks at a passive Stage-1 (fingerprint + behaviour + IP) BEFORE any
challenge renders (docs/REPORT.md §4). This scores that readiness for FREE — no Lever attempt burned — so you
can iterate on IP / fingerprint / warming in seconds instead of on scarce real-posting submits. It imports the
PRODUCTION launch path (browser/pw_engine.launch_playwright) so the score reflects the real config.

What it does:
  1. Egress-IP reputation pre-flight (ipify → IPQualityScore, if JOOBLE_IPQS_API_KEY is set) — pure HTTP.
  2. Asserts no automation tell leaks (navigator.webdriver, __playwright__binding__/__pwInitScripts/cdc_,
     missing window.chrome).
  3. Drives a set of bot-detection pages, EXERCISES each with real cursor/scroll motion (idle pages default to
     "bot"), screenshots them to output/fpcheck/<tool>.png for review, and reads a coarse verdict where it can.

Exits non-zero if a hard check fails, so it wires into a pre-run gate / CI. The browser-scored tools
(CreepJS, incolumitas, pixelscan) are screenshot for human review — the ground truth remains a leverdemo run.
Reads the same env as the bot (JOOBLE_HEADFUL, JOOBLE_CHROME_PATH, JOOBLE_CHROME_NO_SANDBOX, JOOBLE_IPQS_API_KEY, …).
"""

import asyncio
import random
from pathlib import Path

from patchright.async_api import Page

from applyme.browser.human import bezier_path, jittered_point, sample_delay
from applyme.browser.preflight import check_egress_ip
from applyme.browser.pw_engine import launch_playwright
from applyme.config import Settings

# Automation globals that survive navigator.webdriver=false — their presence is a hard stealth tell.
_LEAK_PROBE = (
    "() => navigator.webdriver === true"
    " || !window.chrome"
    " || ['__playwright__binding__','__pwInitScripts','__pw_manual','cdc_']"
    ".some(k => Object.keys(window).some(w => w.includes(k)))"
)

# (label, url) — driven in order; each is exercised with human motion, waited on, then screenshot.
_TOOLS = [
    ("incolumitas", "https://bot.incolumitas.com/"),
    ("vastel-bot", "https://deviceandbrowserinfo.com/are_you_a_bot"),
    ("creepjs", "https://abrahamjuliot.github.io/creepjs/"),
    ("sannysoft", "https://bot.sannysoft.com/"),
    ("pixelscan", "https://pixelscan.net/"),
    ("webrtc", "https://browserleaks.com/webrtc"),
]


async def _exercise(page: Page, rng: random.Random) -> None:
    """Walk a Bézier cursor path + scroll so behavioural scorers see human motion (idle ⇒ classified bot)."""
    try:
        dims = await page.evaluate("() => [window.innerWidth, window.innerHeight]")
        w, h = int(dims[0]) or 1280, int(dims[1]) or 800
        cur = (w * 0.5, h * 0.5)
        for _ in range(3):
            dest = jittered_point(0, 0, w, h, rng)
            for px, py in bezier_path(cur, dest, rng):
                await page.mouse.move(px, py)
                await asyncio.sleep(0.008 + rng.random() * 0.012)
            cur = dest
        await page.mouse.wheel(0, rng.randint(400, 900))
    except Exception as e:  # noqa: BLE001 — diagnostic motion is best-effort
        print(f"    (exercise skipped: {e})")


async def main() -> None:
    s = Settings()
    rng = random.Random(0)
    out = Path("output/fpcheck")
    out.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240 — one-time setup before the browser launches
    failures: list[str] = []

    print("== egress IP reputation ==")
    ip = await check_egress_ip(s.ipqs_api_key.get_secret_value() if s.ipqs_api_key else None)
    print(f"  ip={ip.ip} ok={ip.ok} fraud_score={ip.fraud_score} — {ip.reason}")
    if not ip.ok:
        failures.append(f"IP reputation: {ip.reason}")

    print(f"== launching (headful={s.headful}) ==")
    async with launch_playwright(
        headful=s.headful,
        chrome_path=s.chrome_path,
        no_sandbox=s.chrome_no_sandbox,
        user_data_dir=s.user_data_dir,
        proxy=s.proxy_config(),
        locale=s.browser_locale,
        timezone_id=s.browser_timezone,
    ) as page:
        leaked = await page.evaluate(_LEAK_PROBE)
        print(f"== automation-leak probe: {'LEAK' if leaked else 'clean'} ==")
        if leaked:
            failures.append("automation tell leaked (webdriver / Playwright global / missing window.chrome)")

        for label, url in _TOOLS:
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                await _exercise(page, rng)
                await asyncio.sleep(sample_delay("read_page", rng) + 8)  # let in-page scorers compute
                shot = out / f"{label}.png"
                await page.screenshot(path=str(shot), full_page=True)
                print(f"  {label:12s} → {shot}")
            except Exception as e:  # noqa: BLE001 — one flaky tool must not abort the sweep
                print(f"  {label:12s} → FAILED: {e}")

    print("\n== verdict ==")
    if failures:
        print("FAIL — hard checks:")
        for f in failures:
            print(f"  - {f}")
        print(
            f"Review the screenshots in {out}/ (CreepJS trust≥85 & 0 lies, incolumitas behaviour≥0.5, all-green sannysoft)."
        )
        raise SystemExit(1)
    print(f"PASS hard checks. Review {out}/ for the browser-scored tools, then confirm on a leverdemo sandbox submit.")


if __name__ == "__main__":
    asyncio.run(main())
