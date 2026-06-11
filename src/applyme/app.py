"""Wire the apply flow: load inputs → resolve answers → run the patchright apply over all vacancies.

`resolve_answers` is the engine-agnostic answer mapper (deterministic rules + a guarded LLM fallback);
the per-vacancy browser flow lives in `app_pw` (patchright). `run_command` is the CLI entry point.
"""

from __future__ import annotations

import random
from typing import Any

import structlog

from applyme.answers.rules import map_answers
from applyme.config import Settings
from applyme.errors import PermanentError
from applyme.models import ApplyResult, CandidateProfile, FormSpec, Vacancy

log = structlog.get_logger()


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


async def run_command(args: Any) -> None:
    """CLI entry point: load settings + profile + vacancies, run the patchright apply over all.

    Args:
        args: Parsed argparse namespace from build_parser().
    """
    from pathlib import Path

    from applyme.app_pw import apply_one_pw
    from applyme.config import chrome_version, find_chrome
    from applyme.profile_loader import load_profile, load_vacancies, parse_vacancy
    from applyme.runner import run_all

    settings = Settings()

    # Log Chrome version at startup so a missing/wrong Chrome surfaces before the first apply.
    try:
        chrome_path = find_chrome(settings.chrome_path)
        log.info("chrome", path=chrome_path, version=chrome_version(chrome_path))
    except Exception as _chrome_err:  # noqa: BLE001 — missing Chrome is caught later by launch
        log.warning("chrome_not_found", error=str(_chrome_err))

    profile_path = Path(args.profile)
    profile = load_profile(profile_path, profile_path.parent / "resume.pdf")  # resume is the sibling PDF

    if getattr(args, "url", None):
        v = parse_vacancy(args.url)
        if v is None:
            raise PermanentError(f"not a jobs.lever.co posting URL: {args.url}")
        vacancies = [v]
    else:
        vacancies = load_vacancies(Path(args.vacancies))

    vacancies = vacancies[: getattr(args, "max_applies", settings.max_applies)]
    submit_mode: str = getattr(args, "submit_mode", settings.submit_mode)
    # --headful / --headless override; unset (None) falls back to Settings (env JOOBLE_HEADFUL).
    headful_arg = getattr(args, "headful", None)
    headful: bool = settings.headful if headful_arg is None else headful_arg

    async def apply_fn(v: Vacancy) -> ApplyResult:
        """Apply to one vacancy via patchright; classify any failure rather than crash the batch."""
        rng_seed = random.randint(1, 2**31)
        try:
            return await apply_one_pw(v, profile, settings, submit_mode, headful, Path("output"), rng_seed)
        except Exception as _apply_err:  # noqa: BLE001 — classify the attempt failure, never crash the batch
            log.warning("apply_attempt_failed", company=v.company, posting_id=v.posting_id, error=str(_apply_err))
            raise PermanentError(f"apply attempt failed: {_apply_err}") from _apply_err

    results = await run_all(vacancies, apply_fn, out=Path("output/results.json"))
    for r in results:
        log.info("apply_result", company=r.company, posting_id=r.posting_id, result=r.result_string)
