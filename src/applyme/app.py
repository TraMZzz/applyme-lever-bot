"""Wire the end-to-end apply flow: parse → answer → fill → captcha → submit → verify → evidence."""

from __future__ import annotations

import random
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from applyme import evidence
from applyme.answers.rules import map_answers
from applyme.config import Settings
from applyme.errors import PermanentError
from applyme.lever.form import parse_form_html
from applyme.lever.submit import classify_outcome
from applyme.models import ApplyResult, CandidateProfile, SubmitMode, Vacancy

log = structlog.get_logger()


def _now() -> datetime:
    return datetime.now(UTC)


async def apply_to_vacancy_with_page(
    v: Vacancy,
    profile: CandidateProfile,
    page: Any,
    submit_mode: str,
    *,
    settings: Settings | None = None,
    out_dir: Path = Path("output"),
    rng_seed: int = 0,
) -> ApplyResult:
    """Apply to a single vacancy using an injected page object (real or fake).

    This function is the testable core: browser construction lives in run_command.

    Args:
        v: The vacancy to apply to.
        profile: Candidate profile with resume_path.
        page: A duck-typed page/tab object (zendriver Tab or a test fake).
        submit_mode: One of "dry-run", "sandbox", "real".
        settings: Optional Settings; used for captcha keys and IMAP config.
        out_dir: Root directory for evidence output.
        rng_seed: Seed for reproducibility.

    Returns:
        ApplyResult with the outcome of this application attempt.
    """
    started = _now()
    # 1. Parse the form
    html: str = await page.get_content()
    spec = parse_form_html(html, posting_url=str(v.apply_url))

    # 2. Map answers (deterministic rules; LLM fallback if key available)
    answers, unmapped = map_answers(profile, spec.cards)
    if unmapped and settings and settings.llm_api_key:
        from applyme.answers.llm import answer_question

        llm_key = settings.llm_api_key.get_secret_value()
        profile_summary = f"{profile.full_name}, {profile.location}, {profile.city} {profile.state}"
        for card in spec.cards:
            for f in card.fields:
                if f.input_name in unmapped:
                    ans = await answer_question(llm_key, profile_summary, f.text, f.options)
                    if ans is not None:
                        answers[f.input_name] = ans

    # 3. Dry-run gate: return early before POST
    if submit_mode == SubmitMode.DRY_RUN or submit_mode == "dry-run":
        return ApplyResult(
            posting_url=str(v.url),
            company=v.company,
            posting_id=v.posting_id,
            status="DRY_RUN_READY",
            rng_seed=rng_seed,
            started_at=started,
            finished_at=_now(),
        )

    # 4. Fill the form (uses page's duck-typed interface; no-op on fake pages)
    try:
        await page.fill_form(spec, profile, answers)
    except AttributeError:
        # Fake pages may not implement fill_form — treat as no-op
        pass

    # 5. Captcha: silent pass first, then solve if keys available
    token: str | None = None
    solver_used: str = "none"
    try:
        token = await page.get_captcha_token()
    except AttributeError:
        token = None

    if token is None and settings:
        capsolver_key = settings.capsolver_api_key.get_secret_value() if settings.capsolver_api_key else None
        twocaptcha_key = settings.twocaptcha_api_key.get_secret_value() if settings.twocaptcha_api_key else None
        if capsolver_key or twocaptcha_key:
            from applyme.captcha.base import solve_hcaptcha

            ua: str = ""
            try:
                ua = str(await page.evaluate("navigator.userAgent"))
            except AttributeError:
                pass
            try:
                token = await solve_hcaptcha(
                    page_url=str(v.apply_url),
                    ua=ua,
                    rqdata=spec.rqdata,
                    capsolver_key=capsolver_key,
                    twocaptcha_key=twocaptcha_key,
                )
                solver_used = "capsolver" if capsolver_key else "twocaptcha"
            except PermanentError:
                raise
            except Exception:  # noqa: BLE001
                pass

    # 6. Submit and classify outcome
    final_url: str = str(v.apply_url)
    http_status: int = 200
    body: str = html

    try:
        submit_result = await page.submit(token=token)
        final_url = submit_result.get("final_url", final_url)
        http_status = submit_result.get("status", http_status)
        body = submit_result.get("body", body)
    except AttributeError:
        # Fake pages expose final_url / http_status as attributes
        try:
            final_url = str(page.final_url)
            http_status = int(page.http_status)
            body = await page.get_content()
        except AttributeError:
            pass

    outcome = classify_outcome(final_url=final_url, http_status=http_status, body=body)

    # 7. Best-effort confirmation email (only if IMAP configured)
    confirmation_url: str | None = None
    if settings and settings.imap_user and settings.imap_password:
        from applyme.lever.verify import poll_confirmation

        try:
            confirmation_url = await poll_confirmation(
                host=settings.imap_host,
                user=settings.imap_user,
                password=settings.imap_password.get_secret_value(),
            )
        except Exception:  # noqa: BLE001 — best-effort, never blocks result
            pass

    # 8. Capture evidence (non-fatal)
    evidence_dir = out_dir / v.company / v.posting_id
    ev = await evidence.capture(page, evidence_dir, label="final")

    return ApplyResult(
        posting_url=str(v.url),
        company=v.company,
        posting_id=v.posting_id,
        status=outcome.status,
        reason=outcome.reason,
        flagged_fields=outcome.flagged_fields,
        final_url=final_url,
        http_status=http_status,
        solver_used=solver_used,  # type: ignore[arg-type]
        rng_seed=rng_seed,
        confirmation_email_url=confirmation_url,
        screenshot_paths=[s for s in [ev.get("screenshot")] if s],
        html_snapshot_path=ev.get("html"),
        started_at=started,
        finished_at=_now(),
    )


async def run_command(args: Any) -> None:
    """CLI entry point: load settings + profile + vacancies, launch browser, run all applies.

    Args:
        args: Parsed argparse namespace from build_parser().
    """
    from pathlib import Path as _Path

    settings = Settings()
    from applyme.models import Vacancy as _V
    from applyme.profile_loader import load_profile, load_vacancies

    profile_path = _Path(args.profile)
    # Derive resume path as sibling PDF if not directly loadable
    resume_path = profile_path.parent / "resume.pdf"
    profile = load_profile(profile_path, resume_path)

    if getattr(args, "url", None):
        vacancies = [_V(company="unknown", posting_id="unknown", url=args.url)]
    else:
        vacancies = load_vacancies(_Path(args.vacancies))

    max_applies = getattr(args, "max_applies", settings.max_applies)
    vacancies = vacancies[:max_applies]
    submit_mode: str = getattr(args, "submit_mode", settings.submit_mode)
    headful: bool = getattr(args, "headful", settings.headful)

    from applyme.runner import run_all

    async def apply_fn(v: Vacancy) -> ApplyResult:
        """Launch browser and apply to a single vacancy."""
        try:
            from applyme.browser.engine import launch_browser

            async with launch_browser(headful=headful, chrome_path=settings.chrome_path) as browser:
                tab = await browser.get(v.apply_url)
                return await apply_to_vacancy_with_page(
                    v,
                    profile,
                    tab,
                    submit_mode=submit_mode,
                    settings=settings,
                    out_dir=_Path("output"),
                    rng_seed=random.randint(1, 2**31),
                )
        except Exception as _launch_err:  # noqa: BLE001 — patchright fallback
            log.warning("zendriver_launch_failed", error=str(_launch_err))
            try:
                from patchright.async_api import async_playwright

                async with async_playwright() as pw:
                    browser = await pw.chromium.launch_persistent_context(
                        user_data_dir="",
                        channel="chrome",
                        headless=not headful,
                        no_viewport=True,
                    )
                    page = await browser.new_page()
                    await page.goto(v.apply_url)
                    return await apply_to_vacancy_with_page(
                        v,
                        profile,
                        page,
                        submit_mode=submit_mode,
                        settings=settings,
                        out_dir=_Path("output"),
                        rng_seed=random.randint(1, 2**31),
                    )
            except Exception as _pr_err:  # noqa: BLE001
                raise PermanentError(f"Both zendriver and patchright failed: {_pr_err}") from _pr_err

    results = await run_all(vacancies, apply_fn, out=_Path("output/results.json"))
    for r in results:
        log.info("apply_result", company=r.company, posting_id=r.posting_id, result=r.result_string)
