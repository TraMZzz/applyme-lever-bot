"""Load CandidateProfile from data/profile.json and Vacancy list from a URLs file."""

import json
import re
from pathlib import Path

from applyme.models import CandidateProfile, Vacancy

_LEVER_RE = re.compile(r"jobs\.lever\.co/(?P<company>[^/]+)/(?P<posting_id>[0-9a-f-]+)", re.I)


def load_profile(profile_json: Path, resume_path: Path) -> CandidateProfile:
    data = json.loads(profile_json.read_text())
    data["resume_path"] = str(resume_path)
    return CandidateProfile.model_validate(data)  # extra='forbid' rejects unknown keys (e.g. webhook_url)


def parse_vacancy(url: str) -> Vacancy | None:
    """Parse a jobs.lever.co URL into a Vacancy (company + posting_id), or None if not a Lever URL."""
    m = _LEVER_RE.search(url.strip().strip("<>"))
    if not m:
        return None
    return Vacancy.model_validate(
        {
            "company": m["company"],
            "posting_id": m["posting_id"],
            "url": f"https://jobs.lever.co/{m['company']}/{m['posting_id']}",
        }
    )


def load_vacancies(path: Path) -> list[Vacancy]:
    return [v for line in path.read_text().splitlines() if (v := parse_vacancy(line)) is not None]
