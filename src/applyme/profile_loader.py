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


def load_vacancies(path: Path) -> list[Vacancy]:
    out: list[Vacancy] = []
    for line in path.read_text().splitlines():
        line = line.strip().strip("<>")
        if not line:
            continue
        m = _LEVER_RE.search(line)
        if not m:
            continue
        out.append(
            Vacancy.model_validate(
                {
                    "company": m["company"],
                    "posting_id": m["posting_id"],
                    "url": f"https://jobs.lever.co/{m['company']}/{m['posting_id']}",
                }
            )
        )
    return out
