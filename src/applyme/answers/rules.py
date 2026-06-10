"""Deterministic profile → card-answer mapping; answers constrained to each card's option TEXT."""

from applyme.models import CandidateProfile, Card, CardField


def _pick(options: list[str], want: str) -> str | None:
    """Return the first option whose text contains `want` (case-insensitive), or None."""
    for o in options:
        if want.lower() in o.lower():
            return o
    return None


def _answer(profile: CandidateProfile, f: CardField) -> str | None:
    """Map a single CardField to a profile-derived answer, or return None if unmappable."""
    t = f.text.lower()
    if any(k in t for k in ("authorized", "eligible to work", "legally")):
        return _pick(f.options, "Yes" if profile.work_authorized else "No")
    if "sponsor" in t:
        return _pick(f.options, "No" if not profile.requires_sponsorship else "Yes")
    if "relocate" in t:
        return _pick(f.options, "Yes" if profile.willing_to_relocate else "No")
    if "salary" in t or "compensation" in t:
        return str(profile.expected_salary) if profile.expected_salary else None
    if "state" in t and f.options:
        return _pick(f.options, profile.state) or _pick(f.options, profile.city)
    if any(k in t for k in ("gender", "race", "veteran", "disability")):
        return _pick(f.options, "decline") or (f.options[-1] if f.options else None)
    if any(k in t for k in ("agree", "certify", "submit application")):
        return f.options[0] if f.options else "Yes"
    if "how did you" in t or "hear about" in t:
        return "LinkedIn"
    return None


def map_answers(profile: CandidateProfile, cards: list[Card]) -> tuple[dict[str, str], list[str]]:
    """Map all card fields to profile-derived answers.

    Returns:
        A tuple of (answers dict keyed by input_name, list of unmapped required field names).
    """
    answers: dict[str, str] = {}
    unmapped: list[str] = []
    for card in cards:
        for f in card.fields:
            a = _answer(profile, f)
            if a is not None:
                answers[f.input_name] = a
            elif f.required:
                unmapped.append(f.input_name)
    return answers, unmapped
