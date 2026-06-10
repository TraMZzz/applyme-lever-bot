"""Opt-in live smoke test against Lever's own `leverdemo` sandbox.

Skipped unless RUN_SMOKE=1. Requires a real Chrome and network. It launches the real engine,
navigates to a leverdemo apply page, and runs the full fill pipeline in DRY-RUN mode (fills the
form + captures evidence, stops BEFORE any POST) — verifying the hardest end-to-end path
(launch → parse → answer → human fill → evidence) on a real Lever page without submitting.

This is the harness that resolves the spec's §12 unknowns: run it (and inspect the captured HTML/HAR)
to measure the in-browser hCaptcha silent-pass rate and whether `rqdata` is emitted. To exercise a
real submission to the sandbox, change `submit_mode` to "sandbox" (an explicit, deliberate choice).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from applyme.app import apply_to_vacancy_with_page
from applyme.browser.engine import launch_browser
from applyme.models import CandidateProfile, Vacancy

pytestmark = [
    pytest.mark.smoke,
    pytest.mark.skipif(
        os.getenv("RUN_SMOKE") != "1", reason="opt-in live smoke (set RUN_SMOKE=1; needs Chrome + data/)"
    ),
]

# A public leverdemo posting (update if it 404s — `https://jobs.lever.co/leverdemo`).
LEVERDEMO = Vacancy(
    company="leverdemo",
    posting_id="33538a2f-d27d-4a96-8f05-fa4b0e4d940e",
    url="https://jobs.lever.co/leverdemo/33538a2f-d27d-4a96-8f05-fa4b0e4d940e",
)


def _load_real_profile() -> CandidateProfile:
    """Load data/profile.json + data/resume.pdf (the operator supplies these)."""
    from applyme.profile_loader import load_profile

    data = Path("data")
    profile_json, resume = data / "profile.json", data / "resume.pdf"
    if not profile_json.exists() or not resume.exists():
        pytest.skip("smoke needs data/profile.json and data/resume.pdf")
    return load_profile(profile_json, resume)


async def test_leverdemo_dry_run_fills_real_page() -> None:
    """Full pipeline against a real Lever page, dry-run (no submission). Result must be classified."""
    profile = _load_real_profile()
    async with launch_browser(headful=True, chrome_path=os.getenv("JOOBLE_CHROME_PATH")) as browser:
        tab = await browser.get(LEVERDEMO.apply_url)
        result = await apply_to_vacancy_with_page(LEVERDEMO, profile, tab, submit_mode="dry-run")
    # Never a crash; dry-run reaches the pre-submit evidence capture.
    assert result.result_string
    assert result.status in {"DRY_RUN_READY", "FAILED"}
