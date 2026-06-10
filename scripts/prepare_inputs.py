#!/usr/bin/env python3
"""Convert the provided inputs into the files the bot reads.

ApplyMe provides the raw files in `inputs/`: `profile.md` (candidate JSON embedded in markdown,
with the resume as a URL), `resume.md` (resume text), and `vacancies.md` (Lever URLs). This script
bridges them to what the CLI consumes under `data/`:

  inputs/profile.md   -> data/profile.json   (reshaped to the CandidateProfile model)
  resume URL          -> data/resume.pdf      (downloaded from profile.md's `resume_url`)
  inputs/vacancies.md -> data/vacancies.txt   (URLs, one per line)

Run once before applying:  `uv run python scripts/prepare_inputs.py`
Re-run is idempotent. The candidate `email` is copied as-is from profile.md — edit data/profile.json
afterwards if you want a mailbox you control (see README "Inputs & email"). The resume download is
guarded (https + the known applyme.co host + a size cap); if it fails, place data/resume.pdf yourself.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx

ROOT = Path(__file__).resolve().parent.parent
INPUTS = ROOT / "inputs"
DATA = ROOT / "data"
RESUME_MAX_BYTES = 15 * 1024 * 1024
RESUME_ALLOWED_HOSTS = {"demo-dashboard.applyme.co", "applyme.co"}


def _strip_angles(value: str) -> str:
    """profile.md wraps emails/URLs in markdown angle brackets: `<x>` -> `x`."""
    return value.strip().lstrip("<").rstrip(">") if isinstance(value, str) else value


def _extract_profile_json(md: str) -> dict:
    """Pull the profile-creation JSON object (the one containing personal_information) from the markdown."""
    anchor = md.index("personal_information")
    start = md.rindex("{", 0, anchor)
    depth = 0
    for i in range(start, len(md)):
        if md[i] == "{":
            depth += 1
        elif md[i] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(md[start : i + 1])
    raise ValueError("could not find a balanced profile JSON block in profile.md")


def build_profile(md: str) -> dict:
    """Map ApplyMe's profile-creation payload to the CandidateProfile shape."""
    src = _extract_profile_json(md)
    pi = src.get("personal_information", {})
    misc = src.get("miscellaneous", {})
    full_name = " ".join(p for p in [pi.get("first_name"), pi.get("last_name")] if p).strip()
    profile = {
        "full_name": full_name or "Unknown Candidate",
        "email": _strip_angles(pi.get("email", "")),
        "phone": str(pi.get("phone_number", "")),
        "location": pi.get("address", ""),
        "city": pi.get("city", ""),
        "state": pi.get("state", ""),
        "country": pi.get("country", ""),
        "work_authorized": bool(misc.get("authorised_to_work")) or not src.get("requires_sponsorship", False),
        "requires_sponsorship": bool(src.get("requires_sponsorship", False)),
        "willing_to_relocate": str(misc.get("willing_to_relocate", "No")).strip().lower() == "yes",
        "links": {"LinkedIn": _strip_angles(misc["linkedin_url"])} if misc.get("linkedin_url") else {},
        "skills": [s["skill"] for s in src.get("skills", []) if isinstance(s, dict) and s.get("skill")],
        "work_experience": [
            {
                "company": w.get("company"),
                "title": w.get("job_title"),
                "start": w.get("start_date"),
                "end": w.get("end_date") or None,
                "description": w.get("description"),
            }
            for w in src.get("work_experience", [])
        ],
        "resume_path": str(DATA / "resume.pdf"),
    }
    if misc.get("expected_salary_amount"):
        profile["expected_salary"] = int(misc["expected_salary_amount"])
        profile["expected_salary_currency"] = misc.get("expected_salary_currency", "USD")
    if str(misc.get("total_experience", "")).isdigit():
        profile["total_experience_years"] = int(misc["total_experience"])
    return profile


def download_resume(md: str, dest: Path) -> bool:
    """Download the resume PDF referenced by profile.md's resume_url (host-allowlisted, size-capped)."""
    m = re.search(r'"resume_url"\s*:\s*"<?([^">]+)>?"', md)
    if not m:
        print("! no resume_url in profile.md — place data/resume.pdf manually", file=sys.stderr)
        return False
    url = m.group(1)
    parsed = urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "") not in RESUME_ALLOWED_HOSTS:
        print(
            f"! resume_url host not allowlisted ({parsed.hostname}); download skipped — place data/resume.pdf manually",
            file=sys.stderr,
        )
        return False
    try:
        with httpx.stream("GET", url, follow_redirects=False, timeout=30) as r:
            r.raise_for_status()
            size = 0
            with dest.open("wb") as fh:
                for chunk in r.iter_bytes():
                    size += len(chunk)
                    if size > RESUME_MAX_BYTES:
                        raise ValueError("resume exceeds size cap")
                    fh.write(chunk)
        if dest.read_bytes()[:5] != b"%PDF-":
            print("! downloaded file is not a PDF — verify data/resume.pdf", file=sys.stderr)
        return True
    except (httpx.HTTPError, ValueError, OSError) as e:
        print(f"! resume download failed ({e}); place data/resume.pdf manually", file=sys.stderr)
        return False


def write_vacancies(md: str, dest: Path) -> int:
    urls = re.findall(r"https://jobs\.lever\.co/[^\s<>\)]+", md)
    dest.write_text("\n".join(urls) + ("\n" if urls else ""))
    return len(urls)


def main() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    if not (INPUTS / "profile.md").exists():
        sys.exit(f"missing {INPUTS / 'profile.md'} — place the provided files in inputs/")
    profile_md = (INPUTS / "profile.md").read_text()
    vacancies_md = (INPUTS / "vacancies.md").read_text()

    profile = build_profile(profile_md)
    (DATA / "profile.json").write_text(json.dumps(profile, indent=2))
    print(f"✓ data/profile.json  (name={profile['full_name']!r}, email={profile['email']!r})")

    n = write_vacancies(vacancies_md, DATA / "vacancies.txt")
    print(f"✓ data/vacancies.txt  ({n} Lever URLs)")

    ok = download_resume(profile_md, DATA / "resume.pdf")
    print(f"{'✓' if ok else '✗'} data/resume.pdf  ({'downloaded' if ok else 'MISSING — add it manually'})")


if __name__ == "__main__":
    main()
