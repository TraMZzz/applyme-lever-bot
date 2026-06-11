"""Unit tests for the motion smoke's pure logic (scripts/check_motion.py summarize/check).

The live drive needs Chrome (covered by tests/integration/test_motion_smoke.py); the reduction + pass/fail
thresholds are pure and run in the default gate so the smoke's verdict logic can't silently rot.
"""

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_motion.py"
_spec = importlib.util.spec_from_file_location("check_motion", _SCRIPT)
assert _spec and _spec.loader
check_motion = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_motion)


def test_summarize_counts_and_mean_inter_move_delay() -> None:
    ev = {"moves": [[0, 0, 0], [10, 5, 5], [30, 9, 9]], "wheels": [[0, 100]], "keys": [[0, "KeyA"]]}
    s = check_motion.summarize(ev)
    assert s["moves"] == 3
    assert s["wheels"] == 1
    assert s["keys"] == 1
    assert abs(s["mean_dt_s"] - 0.015) < 1e-9  # (0.010 + 0.020) / 2


def test_check_passes_on_human_like_summary() -> None:
    assert check_motion.check({"moves": 120, "wheels": 3, "keys": 45, "mean_dt_s": 0.012}) == []


def test_check_flags_every_failure_mode() -> None:
    failures = check_motion.check({"moves": 2, "wheels": 0, "keys": 1, "mean_dt_s": 0.5})
    assert len(failures) == 4  # too-few moves, no scroll, too-few keys, mean gap out of human range


def test_check_flags_instant_teleport_motion() -> None:
    # Many samples but zero inter-move delay = teleport, not human motion.
    assert any("inter-move" in f for f in check_motion.check({"moves": 120, "wheels": 3, "keys": 45, "mean_dt_s": 0.0}))
