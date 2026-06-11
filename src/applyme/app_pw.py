"""Patchright single-vacancy apply path.

Mirrors the zendriver flow (warm → /apply → parse → answer → fill → evidence/submit) but on
patchright, whose auto-waiting survives Lever's parseResume re-render. Reuses the engine-agnostic
pieces: parse_form_html, resolve_answers, classify_outcome.
"""

from __future__ import annotations

import asyncio
import contextlib
import random
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

import structlog

if TYPE_CHECKING:
    from patchright.async_api import Page

from applyme.browser.human import jittered_point, sample_delay
from applyme.browser.motion import MotionEngine
from applyme.browser.pw_engine import assert_no_webdriver_leak, launch_playwright
from applyme.config import Settings
from applyme.evidence import redact_html
from applyme.lever.form import parse_form_html
from applyme.lever.pw_fill import pw_fill_form
from applyme.lever.submit import classify_outcome
from applyme.models import ApplyResult, CandidateProfile, SubmitMode, Vacancy

log = structlog.get_logger()


def _now() -> datetime:
    return datetime.now(UTC)


async def _capture(page: object, out_dir: Path, label: str) -> dict[str, str | None]:
    """Screenshot + redacted HTML via Playwright; bounded + best-effort."""
    out_dir.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
    paths: dict[str, str | None] = {"screenshot": None, "html": None}
    shot = out_dir / f"{label}.png"
    for full_page in (True, False):  # full-page first; fall back to viewport when a heavy page times out
        with contextlib.suppress(Exception):
            await page.screenshot(path=str(shot), full_page=full_page, timeout=12000)  # type: ignore[attr-defined]
            paths["screenshot"] = str(shot)
            break
    with contextlib.suppress(Exception):
        snap = out_dir / f"{label}.html"
        html = cast("str", await asyncio.wait_for(page.content(), timeout=15))  # type: ignore[attr-defined]
        snap.write_text(redact_html(html))
        paths["html"] = str(snap)
    return paths


_EMPTY_REQUIRED_JS = """() => {
  const seen = new Set(); const empty = [];
  for (const el of document.querySelectorAll('[required]')) {
    const name = el.name; if (!name || seen.has(name)) continue;
    seen.add(name);
    const grouped = el.type === 'radio' || el.type === 'checkbox';
    const filled = grouped
      ? !!document.querySelector(`[name="${CSS.escape(name)}"]:checked`)
      : !!(el.value && String(el.value).trim());
    if (!filled) empty.push(name);
  }
  return empty;
}"""


async def _empty_required_fields(page: object) -> list[str]:
    """Names of `required` form fields still empty in the DOM (deduped; radio/checkbox judged per group).

    The browser's native HTML5 `required` validation blocks the submit client-side when one is empty —
    no POST, no server error — so `hcaptcha.execute()` never fires and we'd time out on `/apply`. Check
    before clicking Submit and fail honestly naming the fields instead. (DOM is shared across patchright's
    isolated world, so `evaluate` reads live `.value`/`:checked` correctly.)
    """
    pg = cast("Page", page)
    with contextlib.suppress(Exception):
        return cast("list[str]", await pg.evaluate(_EMPTY_REQUIRED_JS)) or []
    return []


async def _human_dwell(page: object, rng: random.Random, motion: MotionEngine) -> None:
    """Genuine pre-action telemetry: real recorded-human cursor drift + scroll so hCaptcha's passive stage
    (which grades mouse velocity/curvature + scroll + time-on-page BEFORE the token is minted) samples
    human-shaped motion.

    The current code's biggest behavioural gap was arriving at /apply with near-zero in-page telemetry; this
    fills the sampling window with retargeted recorded gestures (or the synthetic Bézier fallback when no
    traces are loaded). Best-effort and bounded — never blocks the flow.
    """
    with contextlib.suppress(Exception):
        dims = cast("list[int]", await page.evaluate("() => [window.innerWidth, window.innerHeight]"))  # type: ignore[attr-defined]
        w, h = (dims[0] or 1280), (dims[1] or 800)
        cur = (w * 0.5, h * 0.5)
        for _ in range(rng.randint(2, 4)):
            dest = jittered_point(0, 0, w, h, rng)
            for px, py, sleep_s in motion.path_to(cur, dest, rng):
                await page.mouse.move(px, py)  # type: ignore[attr-defined]
                await asyncio.sleep(sleep_s)
            cur = dest
            await asyncio.sleep(sample_delay("field_think", rng))
        for sleep_s, delta_y in motion.scroll(rng):
            await asyncio.sleep(sleep_s)
            await page.mouse.wheel(0, delta_y)  # type: ignore[attr-defined]
        await asyncio.sleep(sample_delay("read_page", rng))
        await page.mouse.wheel(0, -rng.randint(100, 300))  # type: ignore[attr-defined]


async def _warm(page: object, company: str, apply_url: str, rng: random.Random, motion: MotionEngine) -> None:
    """Cloudflare/hCaptcha warm-up: company page → human dwell → posting → dwell, before /apply.

    Lands on the company page first (never /apply cold) so CF's background JS settles and stamps a clean
    __cf_bm, and drives real cursor/scroll motion so the session carries organic behavioural telemetry into
    the passive captcha stage. The persistent profile (pw_engine) keeps these cookies across the 5 applies.
    """
    with contextlib.suppress(Exception):
        await page.goto(f"https://jobs.lever.co/{company}", wait_until="domcontentloaded", timeout=30000)  # type: ignore[attr-defined]
        await asyncio.sleep(sample_delay("read_page", rng))
        await _human_dwell(page, rng, motion)
        await page.goto(apply_url.removesuffix("/apply"), wait_until="domcontentloaded", timeout=30000)  # type: ignore[attr-defined]
        await asyncio.sleep(sample_delay("read_page", rng))
        await _human_dwell(page, rng, motion)


async def apply_one_pw(
    v: Vacancy,
    profile: CandidateProfile,
    settings: Settings,
    submit_mode: str,
    headful: bool,
    out_dir: Path,
    rng_seed: int,
) -> ApplyResult:
    """Apply to one vacancy via patchright. Dry-run fills + captures evidence and stops before POST."""
    from applyme.app import resolve_answers

    started = _now()
    rng = random.Random(rng_seed)
    ev_dir = out_dir / v.company / v.posting_id
    motion = settings.motion_engine()

    async with launch_playwright(
        headful=headful,
        chrome_path=settings.chrome_path,
        no_sandbox=settings.chrome_no_sandbox,
        user_data_dir=settings.user_data_dir,
        proxy=settings.proxy_config(),
        locale=settings.browser_locale,
        timezone_id=settings.browser_timezone,
    ) as page:
        log.info("pw_apply", at="start", company=v.company, motion=motion.source, submit_mode=submit_mode)
        await _warm(page, v.company, str(v.apply_url), rng, motion)
        log.info("pw_apply", at="warmed")
        await page.goto(str(v.apply_url), wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_selector('[name="resume"]', state="attached", timeout=20000)
        await assert_no_webdriver_leak(page)

        spec = parse_form_html(await page.content(), posting_url=str(v.apply_url))
        log.info("pw_apply", at="parsed", fields=len(spec.standard_fields), cards=len(spec.cards))

        answers, unmapped = await resolve_answers(profile, spec, settings)
        if unmapped:
            ev = await _capture(page, ev_dir, "unmapped")
            return ApplyResult(
                posting_url=str(v.url),
                company=v.company,
                posting_id=v.posting_id,
                status="FAILED",
                reason=f"FORM_SCHEMA_UNMAPPED:{unmapped[0]}",
                flagged_fields=unmapped,
                rng_seed=rng_seed,
                screenshot_paths=[s for s in [ev.get("screenshot")] if s],
                html_snapshot_path=ev.get("html"),
                started_at=started,
                finished_at=_now(),
            )

        await pw_fill_form(page, spec, profile, answers, rng_seed, motion)

        if submit_mode in (SubmitMode.DRY_RUN, "dry-run"):
            ev = await _capture(page, ev_dir, "dry-run")
            return ApplyResult(
                posting_url=str(v.url),
                company=v.company,
                posting_id=v.posting_id,
                status="DRY_RUN_READY",
                rng_seed=rng_seed,
                screenshot_paths=[s for s in [ev.get("screenshot")] if s],
                html_snapshot_path=ev.get("html"),
                started_at=started,
                finished_at=_now(),
            )

        # Pre-submit guard: a `required` field the bot couldn't fill makes the browser block submission
        # client-side (HTML5 validation) — hcaptcha.execute() never fires and we'd time out on /apply.
        # Fail honestly naming the field(s) instead of recording a vague no_thanks_redirect.
        empty_required = await _empty_required_fields(page)
        if empty_required:
            ev = await _capture(page, ev_dir, "unmapped")
            log.warning("pw_apply", at="required_empty", fields=empty_required)
            return ApplyResult(
                posting_url=str(v.url),
                company=v.company,
                posting_id=v.posting_id,
                status="FAILED",
                reason=f"FORM_SCHEMA_UNMAPPED:{empty_required[0]}",
                flagged_fields=empty_required,
                rng_seed=rng_seed,
                screenshot_paths=[s for s in [ev.get("screenshot")] if s],
                html_snapshot_path=ev.get("html"),
                started_at=started,
                finished_at=_now(),
            )

        # Submit (sandbox/real): a pre-execute human pass so the passive captcha stage has live telemetry,
        # then trigger the invisible hCaptcha (silent-pass first) and let Lever's inline script fire the POST.
        log.info("pw_apply", at="submitting")
        await _human_dwell(page, rng, motion)
        solver_used = cast(
            "Literal['none', 'capsolver', 'twocaptcha']", await _submit_with_captcha(page, v, spec, settings)
        )
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("domcontentloaded", timeout=10000)
        final_url = page.url
        # The submit click navigates the page; an unbounded content() read hangs on the in-flight
        # navigation until the outer timeout — and would LOSE a /thanks success. Bound it, and classify
        # on final_url regardless (a /thanks URL is SUCCESS even if the body read times out).
        body = ""
        with contextlib.suppress(Exception):
            body = await asyncio.wait_for(page.content(), timeout=15)
        outcome = classify_outcome(final_url=final_url, http_status=200, body=body)
        # Measured silent-pass KPI: SUCCESS ⇒ the invisible hCaptcha self-passed; CAPTCHA_BLOCKED ⇒ it didn't.
        silent_pass = outcome.status == "SUCCESS"
        captcha_outcome = "silent_pass" if silent_pass else ("blocked" if outcome.status == "CAPTCHA_BLOCKED" else None)
        confirmation_url = await _poll_confirmation(settings)
        ev = await _capture(page, ev_dir, "final")
        return ApplyResult(
            posting_url=str(v.url),
            company=v.company,
            posting_id=v.posting_id,
            status=outcome.status,
            reason=outcome.reason,
            flagged_fields=outcome.flagged_fields,
            final_url=final_url,
            solver_used=solver_used,
            silent_pass=silent_pass,
            captcha_outcome=cast("Literal['silent_pass', 'challenge_rendered', 'blocked'] | None", captcha_outcome),
            confirmation_email_url=confirmation_url,
            rng_seed=rng_seed,
            screenshot_paths=[s for s in [ev.get("screenshot")] if s],
            html_snapshot_path=ev.get("html"),
            started_at=started,
            finished_at=_now(),
        )


# Lever's REAL submit control. The visible button is `type="button"` (class template-btn-submit); its
# application.js click handler runs hcaptcha.execute() then POSTs. The native `button[type=submit]` is
# `class="hidden"` — clicking it directly is a no-op (never actionable), so the captcha never fires.
_SUBMIT_BTN = "button.template-btn-submit"


async def _dismiss_consent(page: object) -> None:
    """Best-effort: clear Lever's cookie-consent bar so it can't intercept the submit click."""
    pg = cast("Page", page)
    with contextlib.suppress(Exception):
        btn = pg.locator(".cc-btn.cc-allow, .cc-btn.cc-deny").first
        if await btn.count() and await btn.is_visible():
            await btn.click(timeout=3000)


async def _trigger_submit(page: object) -> bool:
    """Click Lever's real visible submit button (the one whose JS runs hcaptcha.execute() + POST)."""
    pg = cast("Page", page)
    await _dismiss_consent(page)
    try:
        await pg.locator(_SUBMIT_BTN).first.click(timeout=15000)
        return True
    except Exception as e:  # noqa: BLE001 — a failed click must classify, not crash; log so it's visible
        # A click that fires a navigation can surface as "Frame was detached" / "Execution context was
        # destroyed" — that's a SUCCESSFUL submit trigger (the page is navigating), not a click failure.
        msg = str(e).lower()
        if "detached" in msg or "execution context" in msg or "navigat" in msg:
            log.info("submit_click_navigated", detail=str(e)[:100])
            return True
        log.warning("submit_click_failed", selector=_SUBMIT_BTN, error=str(e))
        return False


async def _challenge_visible(page: object) -> bool:
    """True only if an hCaptcha challenge iframe is actually VISIBLE (escalated), not merely in the DOM.

    Invisible hCaptcha always injects a hidden checkbox-widget iframe whose title contains "challenge";
    presence alone is not a challenge. We require it to be visible to call it an interactive challenge.
    """
    pg = cast("Page", page)
    with contextlib.suppress(Exception):
        loc = pg.locator('iframe[src*="hcaptcha"][title*="challenge"]')
        for i in range(await loc.count()):
            if await loc.nth(i).is_visible():
                return True
    return False


async def _submit_with_captcha(page: object, v: Vacancy, spec: object, settings: Settings) -> str:
    """Click submit and wait for the outcome.

    Lever's inline script runs `hcaptcha.execute()` then fires the native multipart POST. The
    reliable signal is the **navigation**: a silent pass lands on `/<co>/<id>/thanks`. We do NOT poll
    the `h-captcha-response` token (it's gone once the page navigates). A solver is invoked ONLY if an
    interactive challenge iframe actually renders (and a key is set) — and it **fails closed**: with no
    `rqdata`/proxy captured, `solve_hcaptcha` raises `SolverUnavailable`, which we log and record as
    `captcha_blocked` rather than injecting an empty token. The 2026 solver market is delisted/unreliable
    for Lever's invisible Enterprise hCaptcha (docs/REPORT.md §4), so this path is honest insurance only.
    """
    clicked = await _trigger_submit(page)
    with contextlib.suppress(Exception):
        await page.wait_for_url("**/thanks", timeout=20000)  # type: ignore[attr-defined]
    on_thanks = str(page.url).rstrip("/").endswith("/thanks")  # type: ignore[attr-defined]
    log.info("pw_apply", at="submitted", clicked=clicked, on_thanks=on_thanks, url=str(page.url))  # type: ignore[attr-defined]
    if on_thanks:
        return "none"  # silent pass succeeded

    challenge = await _challenge_visible(page)
    ck = settings.capsolver_api_key.get_secret_value() if settings.capsolver_api_key else None
    tk = settings.twocaptcha_api_key.get_secret_value() if settings.twocaptcha_api_key else None
    if not (challenge and (ck or tk)):
        return "none"
    from applyme.captcha.base import solve_hcaptcha
    from applyme.errors import SolverUnavailable

    try:
        async with asyncio.timeout(45):  # bounded: the 2026 solver market is dead for Lever — attempt, never stall
            ua = str(await page.evaluate("navigator.userAgent"))  # type: ignore[attr-defined]
            token, vendor = await solve_hcaptcha(
                page_url=str(v.apply_url),
                ua=ua,
                rqdata=getattr(spec, "rqdata", None),
                capsolver_key=ck,
                twocaptcha_key=tk,
            )
            await page.eval_on_selector('[name="h-captcha-response"]', "(e, v) => { e.value = v; }", token)  # type: ignore[attr-defined]
            await _trigger_submit(page)
            with contextlib.suppress(Exception):
                await page.wait_for_url("**/thanks", timeout=20000)  # type: ignore[attr-defined]
            return vendor
    except SolverUnavailable as e:
        # No usable solver for Lever's invisible Enterprise hCaptcha (delisted vendor / no rqdata
        # captured). Record honestly — never inject an empty/invalid token. → CAPTCHA_BLOCKED.
        log.warning("captcha_solver_unavailable", reason=str(e))
    except Exception as e:  # noqa: BLE001 — bounded best-effort: a dead solver must not stall the run
        log.warning("captcha_solver_failed", error=str(e))
    return "none"


async def _poll_confirmation(settings: Settings) -> str | None:
    """Best-effort: capture Lever's post-submit confirmation email as evidence (never blocks)."""
    if not (settings.imap_user and settings.imap_password):
        return None
    from applyme.lever.verify import poll_confirmation

    with contextlib.suppress(Exception):
        return await poll_confirmation(
            host=settings.imap_host, user=settings.imap_user, password=settings.imap_password.get_secret_value()
        )
    return None
