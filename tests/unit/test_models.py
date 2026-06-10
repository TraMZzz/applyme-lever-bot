from datetime import datetime
from pathlib import Path
import pytest
from pydantic import ValidationError
from applyme.models import (
    SubmitMode, CandidateProfile, Vacancy, Card, CardField, FormSpec, ApplyResult,
)


def test_vacancy_apply_url():
    v = Vacancy(company="aledade", posting_id="abc", url="https://jobs.lever.co/aledade/abc")
    assert v.apply_url == "https://jobs.lever.co/aledade/abc/apply"


def test_profile_forbids_extra_fields():
    with pytest.raises(ValidationError):  # webhook_url must NOT be silently carried
        CandidateProfile(
            full_name="Ethan", email="e@x.com", phone="1", location="NY", city="NY",
            state="NY", country="US", work_authorized=True, requires_sponsorship=False,
            willing_to_relocate=False, resume_path=Path("r.pdf"), webhook_url="http://evil",
        )


def test_result_string_maps_to_brief_literals():
    base = dict(posting_url="u", company="c", posting_id="p", rng_seed=1,
                started_at=datetime(2026, 6, 10), finished_at=datetime(2026, 6, 10))
    assert ApplyResult(status="SUCCESS", **base).result_string == "success"
    assert ApplyResult(status="CAPTCHA_BLOCKED", **base).result_string == "captcha blocked"
    r = ApplyResult(status="FAILED", reason="MISSING_REQUIRED_FIELD:phone", **base)
    assert r.result_string == "failed:MISSING_REQUIRED_FIELD:phone"
