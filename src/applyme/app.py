"""Wire the end-to-end apply flow: parse → answer → fill → captcha → submit → verify → evidence."""

from __future__ import annotations

import asyncio
import contextlib
import random
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

import structlog
import zendriver as zd

from applyme import evidence
from applyme.answers.rules import map_answers
from applyme.browser.actions import HumanActions
from applyme.config import Settings
from applyme.errors import AutofillConflict, PermanentError
from applyme.lever.fill import fill_form
from applyme.lever.form import parse_form_html
from applyme.lever.submit import classify_outcome
from applyme.models import ApplyResult, CandidateProfile, FormSpec, SubmitMode, Vacancy

log = structlog.get_logger()

# The tab interface the flow drives — a real zendriver Tab (or a fake honestly implementing the
# same surface in tests). app.py calls get_content / evaluate / url directly; HumanActions,
# fill_form, and evidence.capture drive select / find / send / save_screenshot. There is no
# AttributeError fall-through: the methods below ARE the page contract.
Page = zd.Tab

# CSS hooks shared by every Lever apply page.
_SUBMIT_SELECTOR = "button[type=submit]"
_HCAPTCHA_RESPONSE_VALUE = "document.querySelector('[name=\"h-captcha-response\"]')?.value || ''"
_HCAPTCHA_CHALLENGE = '!!document.querySelector(\'iframe[src*="hcaptcha"][title*="challenge"]\')'


def _now() -> datetime:
    return datetime.now(UTC)


def _profile_summary(profile: CandidateProfile) -> str:
    """A compact, decision-relevant profile description fed to the LLM for free-form questions."""
    parts = [profile.full_name, f"{profile.city}, {profile.state}, {profile.country}"]
    if profile.total_experience_years is not None:
        parts.append(f"{profile.total_experience_years} years total experience")
    if profile.skills:
        parts.append("skills: " + ", ".join(profile.skills[:12]))
    parts.append(f"work-authorized: {profile.work_authorized}; requires sponsorship: {profile.requires_sponsorship}")
    if profile.expected_salary:
        parts.append(f"expected salary: {profile.expected_salary} {profile.expected_salary_currency}")
    return " | ".join(parts)


async def resolve_answers(
    profile: CandidateProfile,
    spec: FormSpec,
    settings: Settings | None,
) -> tuple[dict[str, str], list[str]]:
    """Map answers via rules, fall back to the LLM for unmapped non-sensitive fields.

    Returns (answers, still_unmapped_required). The LLM is SKIPPED for legally/EEO/eligibility
    questions (is_sensitive) — those must come from profile facts via the rules engine or fail
    closed, never be invented by the model. Its output is validated ∈ options inside answer_question.
    """
    answers, unmapped = map_answers(profile, spec.cards)
    if not unmapped:
        return answers, []
    if not (settings and settings.llm_api_key):
        if settings:
            log.warning(
                "llm_fallback_disabled", unmapped=unmapped, hint="set JOOBLE_LLM_API_KEY for free-form questions"
            )
        return answers, unmapped

    from applyme.answers.llm import answer_question
    from applyme.answers.rules import is_sensitive

    llm_key = settings.llm_api_key.get_secret_value()
    profile_summary = _profile_summary(profile)
    for card in spec.cards:
        for field in card.fields:
            if field.input_name not in unmapped:
                continue
            if is_sensitive(field.text):
                log.warning("llm_skipped_sensitive_question", field=field.input_name, question=field.text[:80])
                continue
            ans = await answer_question(llm_key, profile_summary, field.text, field.options, settings.llm_model)
            if ans is not None:
                answers[field.input_name] = ans
    return answers, [name for name in unmapped if name not in answers]


async def apply_to_vacancy_with_page(
    v: Vacancy,
    profile: CandidateProfile,
    page: Page,
    submit_mode: str,
    *,
    settings: Settings | None = None,
    out_dir: Path = Path("output"),
    rng_seed: int = 0,
) -> ApplyResult:
    """Apply to a single vacancy using an injected tab object (real zendriver Tab or a test fake).

    This function is the testable core: browser construction lives in run_command. It calls real
    functions (parse_form_html, fill_form via HumanActions, solve_hcaptcha, classify_outcome) on
    the tab — there is no duck-typed fall-through path.

    Args:
        v: The vacancy to apply to.
        profile: Candidate profile with resume_path.
        page: A zendriver Tab (or a fake honestly implementing the Page protocol).
        submit_mode: One of "dry-run", "sandbox", "real".
        settings: Optional Settings; used for captcha keys and IMAP config.
        out_dir: Root directory for evidence output.
        rng_seed: Seed for reproducibility.

    Returns:
        ApplyResult with the outcome of this application attempt.
    """
    started = _now()
    tab = page
    human = HumanActions(tab, rng_seed)

    # 0. Wait for the apply form to actually render before parsing/filling. Lever loads the form
    #    after navigation (and any Cloudflare pass); parsing too early sees a form-less page, and
    #    the fill selectors then time out. Use the bounded CDP-DOM select() as the readiness gate —
    #    NOT wait_for_ready_state(), which evaluates and can hang post-transition (a hang is not an
    #    exception, so suppress() can't bound it). Best-effort: a no-op match on the test fake.
    with contextlib.suppress(Exception):
        await tab.select('[name="resume"]', timeout=25)  # type: ignore[attr-defined]

    # 1. Parse the form.
    html: str = await tab.get_content()
    spec = parse_form_html(html, posting_url=str(v.apply_url))
    log.info("apply_step", at="parsed", fields=len(spec.standard_fields), cards=len(spec.cards))

    # 2. Map answers (deterministic rules; LLM fallback when a key is configured).
    answers, unmapped = await resolve_answers(profile, spec, settings)
    log.info("apply_step", at="answers_resolved", answered=len(answers), unmapped=len(unmapped))
    if unmapped:
        ev = await evidence.capture(tab, out_dir / v.company / v.posting_id, label="unmapped")
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

    # 3. Fill the form (resume → settle → override → cards). May raise AutofillConflict.
    try:
        await fill_form(tab, spec, profile, answers, human)
    except AutofillConflict as e:
        ev = await evidence.capture(tab, out_dir / v.company / v.posting_id, label="autofill-conflict")
        return ApplyResult(
            posting_url=str(v.url),
            company=v.company,
            posting_id=v.posting_id,
            status="FAILED",
            reason=f"AUTOFILL_CONFLICT:{e}",
            rng_seed=rng_seed,
            screenshot_paths=[s for s in [ev.get("screenshot")] if s],
            html_snapshot_path=ev.get("html"),
            started_at=started,
            finished_at=_now(),
        )

    # 4. Dry-run gate: capture evidence and stop BEFORE any submit/POST.
    if submit_mode == SubmitMode.DRY_RUN or submit_mode == "dry-run":
        evidence_dir = out_dir / v.company / v.posting_id
        ev = await evidence.capture(tab, evidence_dir, label="dry-run")
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

    # 5. Submit; solve hCaptcha single-flight only if a challenge actually rendered.
    solver_used: Literal["none", "capsolver", "twocaptcha"] = "none"
    await human.click(_SUBMIT_SELECTOR)

    # M4: Settle up to ~3 s for the response field to self-fill before calling a solver.
    response_val = ""
    for _ in range(6):
        response_val = str(await tab.evaluate(_HCAPTCHA_RESPONSE_VALUE))
        if response_val:
            break
        await asyncio.sleep(0.5)

    response_empty = not response_val
    challenge_present = bool(await tab.evaluate(_HCAPTCHA_CHALLENGE))
    if (response_empty or challenge_present) and settings:
        capsolver_key = settings.capsolver_api_key.get_secret_value() if settings.capsolver_api_key else None
        twocaptcha_key = settings.twocaptcha_api_key.get_secret_value() if settings.twocaptcha_api_key else None
        if capsolver_key or twocaptcha_key:
            from applyme.captcha.base import solve_hcaptcha

            ua = str(await tab.evaluate("navigator.userAgent"))
            # I1: unpack (token, vendor) — vendor reflects which service actually solved it.
            token, _vendor = await solve_hcaptcha(
                page_url=str(v.apply_url),
                ua=ua,
                rqdata=spec.rqdata,
                capsolver_key=capsolver_key,
                twocaptcha_key=twocaptcha_key,
            )
            solver_used = cast("Literal['capsolver', 'twocaptcha']", _vendor)
            token_js = token.replace("\\", "\\\\").replace('"', '\\"')
            await tab.evaluate(f'document.querySelector(\'[name="h-captcha-response"]\').value = "{token_js}"')
            await human.click(_SUBMIT_SELECTOR)

    final_url: str = str(tab.url)
    http_status: int = int(str(await tab.evaluate("window.__lastStatus || 200")))
    body: str = await tab.get_content()

    outcome = classify_outcome(final_url=final_url, http_status=http_status, body=body)

    # 6. Best-effort confirmation email (only if IMAP configured); never blocks the result.
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

    # 7. Capture evidence (non-fatal).
    evidence_dir = out_dir / v.company / v.posting_id
    ev = await evidence.capture(tab, evidence_dir, label="final")

    return ApplyResult(
        posting_url=str(v.url),
        company=v.company,
        posting_id=v.posting_id,
        status=outcome.status,
        reason=outcome.reason,
        flagged_fields=outcome.flagged_fields,
        final_url=final_url,
        http_status=http_status,
        solver_used=solver_used,
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

    # M5: Log Chrome version at startup so a missing/wrong Chrome surfaces before the first apply.
    from applyme.config import chrome_version, find_chrome

    try:
        chrome_path = find_chrome(settings.chrome_path)
        log.info("chrome", path=chrome_path, version=chrome_version(chrome_path))
    except Exception as _chrome_err:  # noqa: BLE001 — missing Chrome is caught later by launch_browser
        log.warning("chrome_not_found", error=str(_chrome_err))

    from applyme.profile_loader import load_profile, load_vacancies

    profile_path = _Path(args.profile)
    # Derive resume path as sibling PDF if not directly loadable
    resume_path = profile_path.parent / "resume.pdf"
    profile = load_profile(profile_path, resume_path)

    if getattr(args, "url", None):
        from applyme.profile_loader import parse_vacancy

        v = parse_vacancy(args.url)
        if v is None:
            raise PermanentError(f"not a jobs.lever.co posting URL: {args.url}")
        vacancies = [v]
    else:
        vacancies = load_vacancies(_Path(args.vacancies))

    max_applies = getattr(args, "max_applies", settings.max_applies)
    vacancies = vacancies[:max_applies]
    submit_mode: str = getattr(args, "submit_mode", settings.submit_mode)
    # --headful / --headless override; unset (None) falls back to Settings (env JOOBLE_HEADFUL).
    headful_arg = getattr(args, "headful", None)
    headful: bool = settings.headful if headful_arg is None else headful_arg

    from applyme.runner import run_all

    engine: str = getattr(args, "engine", None) or settings.engine

    async def apply_fn(v: Vacancy) -> ApplyResult:
        """Apply to one vacancy via the configured engine.

        Default is **patchright** (Playwright), which auto-waits through Lever's parseResume
        re-render. **zendriver** (raw CDP) is the stealthiest transport but its Runtime calls hang on
        that re-render, so it is an opt-in alternative.
        """
        rng_seed = random.randint(1, 2**31)
        try:
            if engine == "patchright":
                from applyme.app_pw import apply_one_pw

                return await apply_one_pw(v, profile, settings, submit_mode, headful, _Path("output"), rng_seed)

            from applyme.browser.engine import assert_no_webdriver_leak, launch_browser
            from applyme.browser.warmup import warm_session

            async with launch_browser(
                headful=headful, chrome_path=settings.chrome_path, no_sandbox=settings.chrome_no_sandbox
            ) as browser:
                tab = await warm_session(browser, v.company, str(v.apply_url), rng_seed)
                await tab.get(str(v.apply_url))  # warm dwells on the posting page; open the /apply form
                await assert_no_webdriver_leak(tab)
                try:
                    return await apply_to_vacancy_with_page(
                        v, profile, tab, submit_mode=submit_mode, settings=settings,
                        out_dir=_Path("output"), rng_seed=rng_seed,
                    )
                except Exception:
                    await evidence.capture(tab, _Path("output") / v.company / v.posting_id, label="error")
                    raise
        except Exception as _apply_err:  # noqa: BLE001 — classify the attempt failure, never crash the batch
            log.warning("apply_attempt_failed", company=v.company, posting_id=v.posting_id, error=str(_apply_err))
            raise PermanentError(f"apply attempt failed: {_apply_err}") from _apply_err

    results = await run_all(vacancies, apply_fn, out=_Path("output/results.json"))
    for r in results:
        log.info("apply_result", company=r.company, posting_id=r.posting_id, result=r.result_string)
