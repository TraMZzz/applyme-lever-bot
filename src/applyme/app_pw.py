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
from typing import Literal, cast

import structlog

from applyme.browser.human import bezier_path, jittered_point, sample_delay
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
    with contextlib.suppress(Exception):
        shot = out_dir / f"{label}.png"
        await page.screenshot(path=str(shot), full_page=True)  # type: ignore[attr-defined]
        paths["screenshot"] = str(shot)
    with contextlib.suppress(Exception):
        snap = out_dir / f"{label}.html"
        snap.write_text(redact_html(await page.content()))  # type: ignore[attr-defined]
        paths["html"] = str(snap)
    return paths


async def _human_dwell(page: object, rng: random.Random) -> None:
    """Genuine pre-action telemetry: Bézier cursor drift + scroll so hCaptcha's passive stage (which grades
    mouse velocity/curvature + scroll + time-on-page BEFORE the token is minted) samples human-shaped motion.

    The current code's biggest behavioural gap was arriving at /apply with near-zero in-page telemetry; this
    fills the sampling window. Best-effort and bounded — never blocks the flow.
    """
    with contextlib.suppress(Exception):
        dims = cast("list[int]", await page.evaluate("() => [window.innerWidth, window.innerHeight]"))  # type: ignore[attr-defined]
        w, h = (dims[0] or 1280), (dims[1] or 800)
        cur = (w * 0.5, h * 0.5)
        for _ in range(rng.randint(2, 4)):
            dest = jittered_point(0, 0, w, h, rng)
            for px, py in bezier_path(cur, dest, rng):
                await page.mouse.move(px, py)  # type: ignore[attr-defined]
                await asyncio.sleep(0.006 + rng.random() * 0.012)
            cur = dest
            await asyncio.sleep(sample_delay("field_think", rng))
        await page.mouse.wheel(0, rng.randint(300, 900))  # type: ignore[attr-defined]
        await asyncio.sleep(sample_delay("read_page", rng))
        await page.mouse.wheel(0, -rng.randint(100, 300))  # type: ignore[attr-defined]


async def _warm(page: object, company: str, apply_url: str, rng: random.Random) -> None:
    """Cloudflare/hCaptcha warm-up: company page → human dwell → posting → dwell, before /apply.

    Lands on the company page first (never /apply cold) so CF's background JS settles and stamps a clean
    __cf_bm, and drives real cursor/scroll motion so the session carries organic behavioural telemetry into
    the passive captcha stage. The persistent profile (pw_engine) keeps these cookies across the 5 applies.
    """
    with contextlib.suppress(Exception):
        await page.goto(f"https://jobs.lever.co/{company}", wait_until="domcontentloaded", timeout=30000)  # type: ignore[attr-defined]
        await asyncio.sleep(sample_delay("read_page", rng))
        await _human_dwell(page, rng)
        await page.goto(apply_url.removesuffix("/apply"), wait_until="domcontentloaded", timeout=30000)  # type: ignore[attr-defined]
        await asyncio.sleep(sample_delay("read_page", rng))
        await _human_dwell(page, rng)


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

    async with launch_playwright(
        headful=headful,
        chrome_path=settings.chrome_path,
        no_sandbox=settings.chrome_no_sandbox,
        user_data_dir=settings.user_data_dir,
        proxy=settings.proxy_config(),
        locale=settings.browser_locale,
        timezone_id=settings.browser_timezone,
    ) as page:
        captured: dict[str, str] = {}  # live hCaptcha rqdata, sniffed for the experimental solver test (REPORT §4)
        page.on("response", lambda r: asyncio.create_task(_grab_rqdata(r, captured)))
        await _warm(page, v.company, str(v.apply_url), rng)
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

        await pw_fill_form(page, spec, profile, answers, rng_seed)

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

        # Submit (sandbox/real): a pre-execute human pass so the passive captcha stage has live telemetry,
        # then trigger the invisible hCaptcha (silent-pass first) and let Lever's inline script fire the POST.
        await _human_dwell(page, rng)
        solver_used = cast(
            "Literal['none', 'capsolver', 'twocaptcha', 'captchasonic']",
            await _submit_with_captcha(page, v, spec, settings, rqdata=captured.get("rqdata")),
        )
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("domcontentloaded", timeout=10000)
        final_url = page.url
        body = await page.content()
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


async def _grab_rqdata(resp: object, into: dict[str, str]) -> None:
    """Sniff the live hCaptcha rqdata (`c.req`) from the checksiteconfig/getcaptcha response, for the test."""
    url = str(getattr(resp, "url", ""))
    if "checksiteconfig" not in url and "/getcaptcha/" not in url:
        return
    with contextlib.suppress(Exception):
        data = cast("dict[str, object]", await resp.json())  # type: ignore[attr-defined]
        c = cast("dict[str, object]", data.get("c") or {})
        req = c.get("req")
        if isinstance(req, str) and req:
            into["rqdata"] = req


async def _submit_via_captchasonic(page: object, v: Vacancy, settings: Settings, rqdata: str | None) -> str | None:
    """EXPERIMENTAL falsification test (REPORT §4): mint an out-of-band CaptchaSonic token and inject it.

    Returns ``"captchasonic"`` if the token was injected (whatever the siteverify outcome), else ``None`` to
    fall through to the normal silent-pass path. Gated entirely by ``captchasonic_api_key`` — zero effect
    when unset. A fair test needs solve-IP == submit-IP, so it routes the solve through the same proxy.
    """
    key = settings.captchasonic_api_key.get_secret_value() if settings.captchasonic_api_key else None
    if not key:
        return None
    from applyme.captcha import captchasonic

    try:
        async with asyncio.timeout(180):
            ua = str(await page.evaluate("navigator.userAgent"))  # type: ignore[attr-defined]
            token = await captchasonic.solve(
                page_url=str(v.apply_url), ua=ua, rqdata=rqdata, key=key, proxy=settings.proxy_config()
            )
            # Inject via Lever's own hCaptcha callback (writes h-captcha-response + fires its hidden submit),
            # falling back to setting the field + clicking submit. `via` tells us which path ran.
            via = await page.evaluate(  # type: ignore[attr-defined]
                "(t) => { try { if (typeof onSuccess === 'function') { onSuccess(t); return 'onSuccess'; } } catch (e) {} "
                'const el = document.querySelector(\'[name="h-captcha-response"]\'); if (el) el.value = t; return "field"; }',
                token,
            )
            log.info("captchasonic_injected", via=via, had_rqdata=bool(rqdata))
            with contextlib.suppress(Exception):
                if via == "field":
                    await page.click("button[type=submit]", timeout=15000)  # type: ignore[attr-defined]
                await page.wait_for_url("**/thanks", timeout=25000)  # type: ignore[attr-defined]
        return "captchasonic"
    except Exception as e:  # noqa: BLE001 — experimental; a solver failure must not stall the run
        log.warning("captchasonic_failed", error=str(e))
        return "captchasonic"  # still record that the test fired, so the result is attributable


async def _submit_with_captcha(
    page: object, v: Vacancy, spec: object, settings: Settings, rqdata: str | None = None
) -> str:
    """Click submit and wait for the outcome.

    Lever's inline script runs `hcaptcha.execute()` then fires the native multipart POST. The
    reliable signal is the **navigation**: a silent pass lands on `/<co>/<id>/thanks`. We do NOT poll
    the `h-captcha-response` token (it's gone once the page navigates). A solver is invoked ONLY if an
    interactive challenge iframe actually renders (and a key is set) — and it **fails closed**: with no
    `rqdata`/proxy captured, `solve_hcaptcha` raises `SolverUnavailable`, which we log and record as
    `captcha_blocked` rather than injecting an empty token. The 2026 solver market is delisted/unreliable
    for Lever's invisible Enterprise hCaptcha (docs/REPORT.md §4), so this path is honest insurance only.
    """
    # Experimental: when a CaptchaSonic key is set, test an out-of-band token FIRST (before the native
    # silent-pass navigates away). Gated by the key — no effect on a normal run.
    cs = await _submit_via_captchasonic(page, v, settings, rqdata)
    if cs is not None:
        return cs

    with contextlib.suppress(Exception):
        await page.click("button[type=submit]", timeout=15000)  # type: ignore[attr-defined]
    with contextlib.suppress(Exception):
        await page.wait_for_url("**/thanks", timeout=20000)  # type: ignore[attr-defined]
    if str(page.url).rstrip("/").endswith("/thanks"):  # type: ignore[attr-defined]
        return "none"  # silent pass succeeded

    challenge = False
    with contextlib.suppress(Exception):
        challenge = await page.locator('iframe[src*="hcaptcha"][title*="challenge"]').count() > 0  # type: ignore[attr-defined]
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
            with contextlib.suppress(Exception):
                await page.click("button[type=submit]", timeout=15000)  # type: ignore[attr-defined]
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
