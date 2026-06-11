"""Real recorded-human motion replay (the silent-pass behavioural lever).

hCaptcha's invisible Enterprise stage grades `motionData` — mouse acceleration/curvature/jerk, scroll
dynamics, and keystroke timing — against models trained on real human traces, BEFORE any challenge renders
(see `docs/REPORT.md` §4/§4a). Our default motion is synthetic (`human.bezier_path` + a log-normal delay),
which approximates the *shape* of human motion but not its true micro-structure. This module replays GENUINE
recorded human traces instead: it affine-retargets a recorded gesture (rotate + scale + translate) so its net
displacement maps the required start→end, preserving the recorded velocity profile, overshoot, tremor, and
inter-sample timing — the exact features `motionData` scores.

Recorded traces come from `scripts/record_motion.py` (the operator records on the same Mac that runs headful).
With no traces available the engine falls back to the synthetic path, so the apply flow never regresses — real
traces are an opt-in upgrade, measured for free via `scripts/fingerprint_check.py` and on a `leverdemo` submit.
We do NOT ship fabricated "human" traces: a synthesized trace is not human motion, and claiming otherwise would
defeat the purpose. The bundled default is synthetic; authenticity comes only from a real recording.
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import structlog

from applyme.browser.human import bezier_path, sample_delay

log = structlog.get_logger()

Point = tuple[float, float]
MoveSample = tuple[float, float, float]  # (x, y, sleep_s)
ScrollSample = tuple[float, float]  # (sleep_s, delta_y)
MotionSource = Literal["auto", "recorded", "synthetic"]

# Replay timing is lightly jittered around the recorded dt and clamped, so repeated replays of the same trace
# are not byte-identical and a single slow sample can't stall the flow.
_SLEEP_FLOOR_S = 0.002
_SLEEP_CAP_S = 0.08
_KEYSTROKE_FLOOR_S = 0.02
_KEYSTROKE_CAP_S = 0.60


@dataclass(frozen=True)
class _MousePath:
    """One recorded mouse gesture, stored as per-sample deltas plus its net displacement vector.

    Attributes:
        deltas: Consecutive (dx, dy, dt_s) samples — the raw human velocity profile.
        length: Euclidean length of the net displacement (start→end), used as the retarget scale base.
        angle: Net displacement angle in radians, used as the retarget rotation base.
    """

    deltas: tuple[MoveSample, ...]
    length: float
    angle: float


@dataclass(frozen=True)
class MotionEngine:
    """Replays recorded human motion, retargeted to arbitrary coordinates, with a synthetic fallback.

    Attributes:
        mouse_paths: Recorded mouse gestures available for retargeting (empty ⇒ always falls back).
        flights_s: Recorded inter-keystroke flight times (seconds) sampled for typing rhythm.
        scrolls: Recorded scroll gestures, each a list of (sleep_s, delta_y) wheel events.
        source: "recorded" when real traces drive motion, "synthetic" when forced to the Bézier fallback.
    """

    mouse_paths: tuple[_MousePath, ...]
    flights_s: tuple[float, ...]
    scrolls: tuple[tuple[ScrollSample, ...], ...]
    source: Literal["recorded", "synthetic"]

    @classmethod
    def synthetic(cls) -> MotionEngine:
        """Return an engine with no recorded data — every method uses the synthetic fallback."""
        return cls(mouse_paths=(), flights_s=(), scrolls=(), source="synthetic")

    def path_to(self, start: Point, end: Point, rng: random.Random) -> list[MoveSample]:
        """Return (x, y, sleep_s) samples moving the cursor start→end.

        With recorded traces, picks one and affine-retargets it (rotate by the target-vs-recorded angle, scale
        by the length ratio) so its net displacement lands exactly on `end` while keeping the human micro-shape
        and timing. Without traces (or source="synthetic"), returns a synthetic Bézier path with small delays.
        """
        sx, sy = start
        ex, ey = end
        dx, dy = ex - sx, ey - sy
        target_len = math.hypot(dx, dy)

        if self.source == "synthetic" or not self.mouse_paths or target_len < 1e-6:
            return [(px, py, _jittered_sleep(0.009, rng)) for px, py in bezier_path(start, end, rng)]

        path = rng.choice(self.mouse_paths)
        if path.length < 1e-6 or not path.deltas:
            return [(px, py, _jittered_sleep(0.009, rng)) for px, py in bezier_path(start, end, rng)]

        scale = target_len / path.length
        rot = math.atan2(dy, dx) - path.angle
        cos_r, sin_r = math.cos(rot), math.sin(rot)
        out: list[MoveSample] = []
        x, y = sx, sy
        for step_dx, step_dy, step_dt in path.deltas:
            x += (step_dx * cos_r - step_dy * sin_r) * scale
            y += (step_dx * sin_r + step_dy * cos_r) * scale
            out.append((x, y, _jittered_sleep(step_dt, rng)))
        out[-1] = (ex, ey, out[-1][2])  # land exactly on target despite float drift
        return out

    def keystroke_delay(self, rng: random.Random) -> float:
        """Return the pause before the next character — a sampled recorded flight time, else log-normal."""
        if self.source == "recorded" and self.flights_s:
            return min(_KEYSTROKE_CAP_S, max(_KEYSTROKE_FLOOR_S, rng.choice(self.flights_s)))
        return sample_delay("keystroke", rng)

    def scroll(self, rng: random.Random) -> list[ScrollSample]:
        """Return a scroll gesture as (sleep_s, delta_y) wheel events — recorded if available, else one nudge."""
        if self.source == "recorded" and self.scrolls:
            return list(rng.choice(self.scrolls))
        return [(0.0, float(rng.randint(300, 900)))]


def _jittered_sleep(dt: float, rng: random.Random) -> float:
    """Clamp a recorded/synthetic inter-sample delay with ±15% jitter so replays aren't identical."""
    return min(_SLEEP_CAP_S, max(_SLEEP_FLOOR_S, dt * (0.85 + rng.random() * 0.30)))


def _path_from_points(points: list[list[float]]) -> _MousePath | None:
    """Convert a recorded [[t_ms, x, y], …] gesture into a _MousePath; None if too short/degenerate."""
    if len(points) < 3:
        return None
    deltas: list[MoveSample] = []
    for (t0, x0, y0), (t1, x1, y1) in zip(points, points[1:], strict=False):
        dt = max(0.0, (t1 - t0) / 1000.0)
        deltas.append((x1 - x0, y1 - y0, dt))
    net_x = points[-1][1] - points[0][1]
    net_y = points[-1][2] - points[0][2]
    length = math.hypot(net_x, net_y)
    if length < 8.0:  # a near-stationary fidget is no use as a retargetable path
        return None
    return _MousePath(deltas=tuple(deltas), length=length, angle=math.atan2(net_y, net_x))


def load_motion_engine(path: Path | None, source: MotionSource = "auto") -> MotionEngine:
    """Build a MotionEngine from a recorded-trace JSON file.

    Args:
        path: Path to the JSON written by `scripts/record_motion.py`, or None.
        source: "synthetic" forces the Bézier fallback; "recorded" requires the file (logs + falls back to
            synthetic if absent/empty); "auto" uses the file when present and synthetic otherwise.

    Returns:
        A `recorded` engine when usable traces load, else a `synthetic` one (the apply flow never regresses).
    """
    if source == "synthetic":
        return MotionEngine.synthetic()
    if path is None or not path.exists():
        if source == "recorded":
            log.warning("motion", at="traces_missing", path=str(path), fallback="synthetic")
        return MotionEngine.synthetic()

    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("motion", at="traces_unreadable", path=str(path), error=str(exc), fallback="synthetic")
        return MotionEngine.synthetic()

    mouse_paths = tuple(p for seg in raw.get("mouse_paths", []) if (p := _path_from_points(seg.get("pts", []))))
    flights_s = tuple(
        max(0.0, ms / 1000.0) for ms in raw.get("keystrokes", {}).get("flight_ms", []) if isinstance(ms, int | float)
    )
    scrolls = tuple(tuple(_scroll_events(seg.get("events", []))) for seg in raw.get("scrolls", []) if seg.get("events"))
    scrolls = tuple(s for s in scrolls if s)

    if not mouse_paths:
        log.warning("motion", at="no_usable_paths", path=str(path), fallback="synthetic")
        return MotionEngine.synthetic()

    log.info(
        "motion", at="loaded", source="recorded", paths=len(mouse_paths), flights=len(flights_s), scrolls=len(scrolls)
    )
    return MotionEngine(mouse_paths=mouse_paths, flights_s=flights_s, scrolls=scrolls, source="recorded")


def _scroll_events(events: list[list[float]]) -> list[ScrollSample]:
    """Convert a recorded [[t_ms, delta_y], …] scroll into (sleep_s, delta_y) replay events."""
    out: list[ScrollSample] = []
    prev_t: float | None = None
    for t_ms, delta_y in events:
        sleep_s = 0.0 if prev_t is None else min(_SLEEP_CAP_S, max(0.0, (t_ms - prev_t) / 1000.0))
        out.append((sleep_s, float(delta_y)))
        prev_t = t_ms
    return out
