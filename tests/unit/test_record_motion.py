"""Unit tests for the motion recorder's pure segmentation/timing helpers (scripts/record_motion.py).

The recorder needs a real browser to capture events, but its post-processing (segmenting the raw event stream
into gestures and deriving keystroke timings) is pure and testable — and its output format must round-trip
through `browser.motion.load_motion_engine`, which these tests assert directly.
"""

import importlib.util
import json
from pathlib import Path

from applyme.browser.motion import load_motion_engine

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "record_motion.py"
_spec = importlib.util.spec_from_file_location("record_motion", _SCRIPT)
assert _spec and _spec.loader
record_motion = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(record_motion)


def test_segment_moves_splits_on_idle_gap_and_drops_short_runs() -> None:
    moves = [
        [0, 1, 1],
        [10, 2, 2],
        [20, 3, 3],  # gesture A (3 pts)
        [600, 9, 9],
        [610, 10, 10],
        [620, 11, 11],
        [630, 12, 12],  # gesture B after a 580ms gap
        [1300, 50, 50],
        [1310, 51, 51],  # trailing 2-pt run after another gap → dropped (<3)
    ]
    paths = record_motion._segment_moves(moves)
    assert [len(p["pts"]) for p in paths] == [3, 4]


def test_segment_wheels_splits_on_idle_gap() -> None:
    wheels = [[0, 120], [16, 120], [32, 90], [800, -100], [816, -120]]
    scrolls = record_motion._segment_wheels(wheels)
    assert [len(s["events"]) for s in scrolls] == [3, 2]


def test_keystroke_timings_derives_flight_and_dwell() -> None:
    keydowns = [[0, "KeyH"], [120, "KeyE"], [250, "KeyL"]]
    keyups = [[80, "KeyH"], [200, "KeyE"], [330, "KeyL"]]
    timings = record_motion._keystroke_timings(keydowns, keyups)
    assert timings["flight_ms"] == [120, 130]
    assert timings["dwell_ms"] == [80, 80, 80]


def test_keystroke_timings_filters_implausible_durations() -> None:
    # A >2s flight (idle break) and a held key (>1s dwell) are dropped as noise.
    keydowns = [[0, "KeyA"], [5000, "KeyB"], [5100, "KeyC"]]
    keyups = [[3000, "KeyA"], [5080, "KeyB"], [5160, "KeyC"]]
    timings = record_motion._keystroke_timings(keydowns, keyups)
    assert timings["flight_ms"] == [100]  # 5000→0 dropped (>2000); 5100-5000=100 kept
    assert 3000 not in timings["dwell_ms"]  # KeyA held 3s → dropped


def test_recorder_output_round_trips_into_motion_engine(tmp_path: Path) -> None:
    """The recorder's JSON shape must be directly loadable as a recorded MotionEngine (schema contract)."""
    moves = [[i * 16, 100 + i * 6, 200 + i * 4] for i in range(12)]
    payload = {
        "version": 1,
        "mouse_paths": record_motion._segment_moves(moves),
        "scrolls": record_motion._segment_wheels([[0, 120], [16, 130]]),
        "keystrokes": record_motion._keystroke_timings([[0, "KeyA"], [110, "KeyB"]], [[80, "KeyA"], [190, "KeyB"]]),
    }
    p = tmp_path / "traces.json"
    p.write_text(json.dumps(payload))

    eng = load_motion_engine(p)
    assert eng.source == "recorded"
    assert len(eng.mouse_paths) == 1
    assert eng.flights_s == (0.110,)
