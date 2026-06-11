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

from applyme.browser.human import sample_delay
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


async def _warm(page: object, company: str, apply_url: str, rng: random.Random) -> None:
    """Light Cloudflare warm-up: company page → dwell → posting, before /apply."""
    with contextlib.suppress(Exception):
        await page.goto(f"https://jobs.lever.co/{company}", wait_until="domcontentloaded", timeout=30000)  # type: ignore[attr-defined]
        await asyncio.sleep(sample_delay("read_page", rng))
        await page.goto(apply_url.removesuffix("/apply"), wait_until="domcontentloaded", timeout=30000)  # type: ignore[attr-defined]
        await asyncio.sleep(sample_delay("read_page", rng))


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
        headful=headful, chrome_path=settings.chrome_path, no_sandbox=settings.chrome_no_sandbox
    ) as page:
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

        # Submit (sandbox/real): trigger the invisible hCaptcha; solve only if a challenge renders
        # (silent-pass first), then let Lever's inline script fire the native multipart POST.
        solver_used = cast(
            "Literal['none', 'capsolver', 'twocaptcha']", await _submit_with_captcha(page, v, spec, settings)
        )
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("domcontentloaded", timeout=10000)
        final_url = page.url
        body = await page.content()
        outcome = classify_outcome(final_url=final_url, http_status=200, body=body)
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
            confirmation_email_url=confirmation_url,
            rng_seed=rng_seed,
            screenshot_paths=[s for s in [ev.get("screenshot")] if s],
            html_snapshot_path=ev.get("html"),
            started_at=started,
            finished_at=_now(),
        )


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
