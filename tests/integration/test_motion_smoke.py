"""Motion smoke (needs a real Chrome). Run: pytest -m integration.

Drives the production motion engine on a capture page and asserts the browser observed real, human-shaped
motion — the end-to-end check the unit tests (stub page) can't do. Reuses scripts/check_motion.py so the
smoke and the standalone `uv run python scripts/check_motion.py` share one implementation.
"""

import importlib.util
import random
from pathlib import Path

import pytest

from applyme.browser.pw_engine import launch_playwright
from applyme.config import Settings

pytestmark = pytest.mark.integration

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_motion.py"
_spec = importlib.util.spec_from_file_location("check_motion", _SCRIPT)
assert _spec and _spec.loader
check_motion = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_motion)


async def test_motion_engine_drives_real_browser() -> None:
    """The motion engine must make real Chrome emit human-shaped mouse/scroll/key events."""
    s = Settings()
    motion = s.motion_engine()
    async with launch_playwright(headful=s.headful, chrome_path=s.chrome_path, no_sandbox=s.chrome_no_sandbox) as page:
        ev = await check_motion.drive_and_capture(page, motion, random.Random(0))
    summary = check_motion.summarize(ev)
    failures = check_motion.check(summary)
    assert not failures, f"{failures} | summary={summary}"
