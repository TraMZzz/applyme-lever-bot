import math
import random

from applyme.browser.actions import jittered_point
from applyme.browser.human import DELAY_CLASSES, bezier_path, sample_delay


def test_path_starts_and_ends_at_targets():
    rng = random.Random(7)
    path = bezier_path((0.0, 0.0), (100.0, 50.0), rng)
    assert path[0] == (0.0, 0.0)
    assert math.isclose(path[-1][0], 100.0, abs_tol=1.0) and math.isclose(path[-1][1], 50.0, abs_tol=1.0)
    assert len(path) >= 12


def test_path_is_not_a_straight_line():
    rng = random.Random(7)
    path = bezier_path((0.0, 0.0), (100.0, 0.0), rng)
    max_dev = max(abs(y) for _, y in path)  # straight line on y=0 would have 0 deviation
    assert max_dev > 1.0


def test_delay_within_clamp_and_reproducible():
    for action, (_median, _sigma, lo, hi) in DELAY_CLASSES.items():
        d = sample_delay(action, random.Random(1))
        assert lo <= d <= hi
    assert sample_delay("keystroke", random.Random(42)) == sample_delay("keystroke", random.Random(42))


def test_jittered_point_stays_within_bounds():
    left, top, width, height = 100.0, 50.0, 80.0, 20.0
    rng = random.Random(3)
    for _ in range(500):
        x, y = jittered_point(left, top, width, height, rng)
        assert left <= x <= left + width
        assert top <= y <= top + height
