"""Deterministic profile → card-answer mapping; answers constrained to each card's option TEXT."""

import re

from applyme.models import CandidateProfile, Card, CardField

# Questions whose factual answer is legally/EEO/eligibility-sensitive. The LLM fallback must NEVER
# guess these (a fabricated eligibility claim to a real employer is an integrity risk). They are
# answered only from CandidateProfile facts via _answer() below, or left unmapped to fail closed.
_SENSITIVE = re.compile(
    r"\b(sponsor|visa|work\s+authoriz|authorized\s+to\s+work|legally\s+(?:eligible|authorized)|"
    r"eligible\s+to\s+work|citizen|citizenship|green\s+card|permanent\s+resident|itar|export\s+control|"
    r"security\s+clearance|clearance|gender|race|ethnic|hispanic|latino|veteran|disab|age|"
    r"date\s+of\s+birth|criminal|felony|conviction|background\s+check)\b",
    re.I,
)


def is_sensitive(text: str) -> bool:
    """True if a question concerns legal eligibility / protected-class / EEO facts.

    The apply flow uses this to skip the LLM fallback for such questions: a factual eligibility
    answer must come from the profile (via _answer), never be invented by the model.
    """
    return bool(_SENSITIVE.search(text))


def _pick(options: list[str], want: str) -> str | None:
    """Return the first option whose text contains `want` (case-insensitive), or None."""
    for o in options:
        if want.lower() in o.lower():
            return o
    return None


def _yes_no(f: CardField, yes: bool) -> str | None:
    """Answer a Yes/No eligibility fact from the profile.

    For a choice field, pick the option containing Yes/No; for a free-text field (no options),
    answer the bare word directly — the value is a profile FACT, not an LLM guess, so it should be
    given rather than left unmapped (which would fail closed under the sensitive-question guard).
    """
    want = "Yes" if yes else "No"
    return want if not f.options else _pick(f.options, want)


def _answer(profile: CandidateProfile, f: CardField) -> str | None:
    """Map a single CardField to a profile-derived answer, or return None if unmappable.

    Branch order matters — earlier branches win. Sponsorship is tested before work-authorization
    (a sponsorship question may contain "legally"); consent/acknowledgement is tested before the
    state branch ("statements" contains the substring "state"); the state branch is gated to a real
    dropdown so it cannot hijack a free-text or consent field.
    """
    t = f.text.lower()
    if "sponsor" in t or "visa" in t:
        return _yes_no(f, not profile.requires_sponsorship if "without" in t else profile.requires_sponsorship)
    if any(k in t for k in ("authorized to work", "eligible to work", "work authoriz", "legally")):
        return _yes_no(f, profile.work_authorized)
    if "relocate" in t:
        return _yes_no(f, profile.willing_to_relocate)
    if "salary" in t or "compensation" in t:
        return str(profile.expected_salary) if profile.expected_salary else None
    if any(k in t for k in ("i agree", "i certify", "i acknowledge", "i consent", "submit application")):
        return f.options[0] if f.options else "Yes"
    if f.field_type == "dropdown" and re.search(r"\bstate\b", t):
        return _pick(f.options, profile.state) or _pick(f.options, profile.city)
    if any(k in t for k in ("gender", "race", "ethnic", "veteran", "disab")):
        return _pick(f.options, "decline") or (f.options[-1] if f.options else None)
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
