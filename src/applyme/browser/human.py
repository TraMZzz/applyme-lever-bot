"""Human-like timing + mouse paths (stdlib only). zendriver's mouse_move is straight-line; this replaces it."""
import math
import random

# (median_s, sigma_log, floor_s, cap_s) — right-skewed human timing
DELAY_CLASSES: dict[str, tuple[float, float, float, float]] = {
    "keystroke": (0.14, 0.50, 0.05, 0.60),
    "field_think": (0.90, 0.55, 0.30, 4.00),
    "read_page": (2.40, 0.45, 1.20, 6.00),
    "pre_submit": (1.20, 0.50, 0.60, 4.00),
    "inter_apply": (45.0, 0.40, 20.0, 120.0),
}

Point = tuple[float, float]


def sample_delay(action: str, rng: random.Random) -> float:
    """Sample a clamped log-normal delay for the given action class."""
    median, sigma, lo, hi = DELAY_CLASSES[action]
    return min(hi, max(lo, rng.lognormvariate(math.log(median), sigma)))


def bezier_path(start: Point, end: Point, rng: random.Random) -> list[Point]:
    """Curved cubic-Bézier path with a perpendicular 'bow' and smoothstep easing."""
    (x0, y0), (x1, y1) = start, end
    dist = math.hypot(x1 - x0, y1 - y0)
    steps = max(12, min(60, int(dist / 8) + rng.randint(8, 18)))
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    nx, ny = -(y1 - y0), (x1 - x0)
    norm = math.hypot(nx, ny) or 1.0
    bow = rng.gauss(0, dist * 0.12)
    c1 = (x0 + (mx - x0) * 0.6 + nx / norm * bow * 0.5, y0 + (my - y0) * 0.6 + ny / norm * bow * 0.5)
    c2 = (x1 + (mx - x1) * 0.6 + nx / norm * bow * 0.5, y1 + (my - y1) * 0.6 + ny / norm * bow * 0.5)
    out: list[Point] = []
    for i in range(steps):
        t = i / (steps - 1)
        t = t * t * (3 - 2 * t)  # smoothstep
        mt = 1 - t
        bx = mt**3 * x0 + 3 * mt**2 * t * c1[0] + 3 * mt * t**2 * c2[0] + t**3 * x1
        by = mt**3 * y0 + 3 * mt**2 * t * c1[1] + 3 * mt * t**2 * c2[1] + t**3 * y1
        out.append((bx, by))
    out[0], out[-1] = start, end
    return out
