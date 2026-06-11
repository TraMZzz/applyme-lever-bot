"""Unit tests for the recorded-human motion engine (browser/motion.py)."""

import json
import math
import random
from pathlib import Path

from applyme.browser.motion import (
    _KEYSTROKE_CAP_S,
    _KEYSTROKE_FLOOR_S,
    _SLEEP_CAP_S,
    _SLEEP_FLOOR_S,
    MotionEngine,
    load_motion_engine,
)

_RECORDED = {
    "version": 1,
    "mouse_paths": [{"pts": [[0, 100, 100], [10, 120, 130], [20, 160, 180], [30, 200, 240]]}],
    "scrolls": [{"events": [[0, 120], [16, 120], [32, 90]]}],
    "keystrokes": {"flight_ms": [120, 95, 140], "dwell_ms": [80, 90]},
}


def _write(tmp_path: Path, payload: object) -> Path:
    p = tmp_path / "traces.json"
    p.write_text(json.dumps(payload))
    return p


def test_synthetic_path_lands_exactly_on_target() -> None:
    eng = MotionEngine.synthetic()
    rng = random.Random(0)
    samples = eng.path_to((0.0, 0.0), (300.0, 400.0), rng)
    assert eng.source == "synthetic"
    assert samples
    assert samples[-1][0] == 300.0
    assert samples[-1][1] == 400.0
    assert all(_SLEEP_FLOOR_S <= s <= _SLEEP_CAP_S for _, _, s in samples)


def test_recorded_engine_loads(tmp_path: Path) -> None:
    eng = load_motion_engine(_write(tmp_path, _RECORDED))
    assert eng.source == "recorded"
    assert len(eng.mouse_paths) == 1
    assert eng.flights_s == (0.120, 0.095, 0.140)


def test_recorded_path_retargets_to_exact_endpoint(tmp_path: Path) -> None:
    eng = load_motion_engine(_write(tmp_path, _RECORDED))
    rng = random.Random(1)
    start, end = (0.0, 0.0), (300.0, 400.0)
    samples = eng.path_to(start, end, rng)
    # One sample per recorded delta (= pts - 1); the human micro-structure is preserved, not regenerated.
    assert len(samples) == 3
    assert math.isclose(samples[-1][0], end[0], abs_tol=1e-6)
    assert math.isclose(samples[-1][1], end[1], abs_tol=1e-6)


def test_recorded_keystroke_delay_uses_recorded_flights(tmp_path: Path) -> None:
    eng = load_motion_engine(_write(tmp_path, _RECORDED))
    rng = random.Random(2)
    for _ in range(20):
        d = eng.keystroke_delay(rng)
        assert _KEYSTROKE_FLOOR_S <= d <= _KEYSTROKE_CAP_S
        assert any(math.isclose(d, f) for f in (0.120, 0.095, 0.140))


def test_recorded_scroll_replays_recorded_events(tmp_path: Path) -> None:
    eng = load_motion_engine(_write(tmp_path, _RECORDED))
    events = eng.scroll(random.Random(3))
    assert events[0] == (0.0, 120.0)
    assert [dy for _, dy in events] == [120.0, 120.0, 90.0]


def test_synthetic_source_forces_fallback_even_with_a_file(tmp_path: Path) -> None:
    eng = load_motion_engine(_write(tmp_path, _RECORDED), source="synthetic")
    assert eng.source == "synthetic"
    assert eng.mouse_paths == ()


def test_missing_file_falls_back_to_synthetic() -> None:
    assert load_motion_engine(Path("/no/such/traces.json")).source == "synthetic"
    assert load_motion_engine(None).source == "synthetic"


def test_unreadable_or_empty_traces_fall_back(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json")
    assert load_motion_engine(bad).source == "synthetic"
    # Valid JSON but no usable (long-enough) gestures → synthetic.
    stationary = {"mouse_paths": [{"pts": [[0, 10, 10], [5, 11, 11], [9, 10, 10]]}]}
    assert load_motion_engine(_write(tmp_path, stationary)).source == "synthetic"


def test_recorded_path_preserves_curvature_not_a_straight_lerp(tmp_path: Path) -> None:
    # A curved recorded gesture, retargeted, must deviate from the straight start→end line — i.e. the human
    # micro-structure is preserved, not replaced by a naive interpolation.
    curved = {"mouse_paths": [{"pts": [[i * 16, i * 10, 40 * math.sin(i / 3)] for i in range(16)]}]}
    eng = load_motion_engine(_write(tmp_path, curved))
    start, end = (0.0, 0.0), (400.0, 0.0)
    samples = eng.path_to(start, end, random.Random(0))
    # Perpendicular distance of each sample from the straight start→end line (here the x-axis) = |y|.
    assert max(abs(y) for _, y, _ in samples) > 5.0
    assert samples[-1] != samples[0]


async def test_human_dwell_drives_mouse_via_engine(tmp_path: Path) -> None:
    from applyme.app_pw import _human_dwell

    eng = load_motion_engine(_write(tmp_path, _RECORDED))
    page = _StubPage()
    await _human_dwell(page, random.Random(0), eng)
    assert page.mouse.moves, "dwell produced no mouse motion"
    assert all(math.isfinite(x) and math.isfinite(y) for x, y in page.mouse.moves)
    assert page.mouse.wheels, "dwell produced no scroll"


async def test_pw_fill_human_move_consumes_engine_and_lands_on_target(tmp_path: Path) -> None:
    from applyme.lever.pw_fill import _human_move

    eng = load_motion_engine(_write(tmp_path, _RECORDED))
    page = _StubPage()
    end = await _human_move(page, random.Random(0), eng, (10.0, 10.0), 250.0, 300.0)
    assert end == (250.0, 300.0)
    assert page.mouse.moves[-1] == (250.0, 300.0)


class _StubMouse:
    def __init__(self) -> None:
        self.moves: list[tuple[float, float]] = []
        self.wheels: list[tuple[float, float]] = []

    async def move(self, x: float, y: float) -> None:
        self.moves.append((x, y))

    async def wheel(self, dx: float, dy: float) -> None:
        self.wheels.append((dx, dy))


class _StubPage:
    def __init__(self) -> None:
        self.mouse = _StubMouse()

    async def evaluate(self, _expr: str) -> list[int]:
        return [1200, 800]
