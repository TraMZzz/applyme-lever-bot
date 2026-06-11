"""Opt-in live smoke against Lever's own `leverdemo` sandbox via patchright.

Skipped unless RUN_SMOKE=1. Needs a real Chrome + network + data/. Runs the full patchright apply in
DRY-RUN (fills the form + captures evidence, stops BEFORE any POST) on a real leverdemo apply page —
the hardest end-to-end path. To exercise a real submission, change submit_mode to "sandbox".
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from applyme.app_pw import apply_one_pw
from applyme.config import Settings
from applyme.models import Vacancy

pytestmark = [
    pytest.mark.smoke,
    pytest.mark.skipif(
        os.getenv("RUN_SMOKE") != "1", reason="opt-in live smoke (set RUN_SMOKE=1; needs Chrome + data/)"
    ),
]

LEVERDEMO = Vacancy(
    company="leverdemo",
    posting_id="33538a2f-d27d-4a96-8f05-fa4b0e4d940e",
    url="https://jobs.lever.co/leverdemo/33538a2f-d27d-4a96-8f05-fa4b0e4d940e",
)


def _load_real_profile():
    """Load data/profile.json + data/resume.pdf (the operator supplies these)."""
    from applyme.profile_loader import load_profile

    data = Path("data")
    if not (data / "profile.json").exists() or not (data / "resume.pdf").exists():
        pytest.skip("smoke needs data/profile.json and data/resume.pdf")
    return load_profile(data / "profile.json", data / "resume.pdf")


async def test_leverdemo_dry_run_fills_real_page() -> None:
    """Full patchright pipeline against a real Lever page, dry-run (no submission). Must classify."""
    settings = Settings()
    profile = _load_real_profile()
    result = await apply_one_pw(
        LEVERDEMO,
        profile,
        settings,
        submit_mode="dry-run",
        headful=settings.headful,
        out_dir=Path("output"),
        rng_seed=1,
    )
    assert result.result_string
    assert result.status in {"DRY_RUN_READY", "FAILED"}
