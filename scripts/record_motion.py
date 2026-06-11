#!/usr/bin/env python3
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Record REAL human mouse/scroll/keystroke motion for the silent-pass behavioural lever.

    uv run python scripts/record_motion.py            # records ~90s, writes data/motion/human_traces.json
    JOOBLE_MOTION_SECONDS=120 uv run python scripts/record_motion.py

hCaptcha's invisible Enterprise stage grades `motionData` (mouse acceleration/curvature/jerk, scroll dynamics,
keystroke timing) against models trained on real human traces — see `docs/REPORT.md` §4a. The bot's default
motion is a synthetic Bézier approximation; this captures GENUINE human motion so `browser/motion.py` can replay
it (affine-retargeted) into the captcha's sampling window. Run it on the same Mac that runs the headful apply.

It opens a real Chrome window (the production launch path) with a capture page: move the mouse around naturally,
scroll up/down, and type a few sentences in the box for the countdown. Nothing is uploaded — events are captured
locally and written to JSON. Point the bot at it with `JOOBLE_MOTION_TRACES=data/motion/human_traces.json`.
"""

import asyncio
import json
import os
from pathlib import Path

from applyme.browser.pw_engine import launch_playwright
from applyme.config import Settings

OUT = Path("data/motion/human_traces.json")
SECONDS = int(os.environ.get("JOOBLE_MOTION_SECONDS", "90"))
IDLE_GAP_MS = 400  # a pause longer than this splits the continuous stream into separate gestures

_CAPTURE_HTML = """
<!doctype html><html><head><meta charset=utf-8><title>record motion</title>
<style>body{font:16px system-ui;margin:0;height:100vh;background:#0b1021;color:#e7ecff;
display:flex;flex-direction:column;align-items:center;justify-content:center;gap:18px}
#t{font-size:48px;font-variant-numeric:tabular-nums}textarea{width:60ch;height:7em;font:16px ui-monospace;
padding:10px;border-radius:8px;border:1px solid #36406b;background:#121a36;color:#e7ecff}
.hint{opacity:.7;max-width:60ch;text-align:center;line-height:1.5}</style></head>
<body>
<div id=t>--</div>
<div class=hint>Move the mouse around naturally, scroll up and down, and type the sentences below until the
timer hits zero. This captures your genuine motion locally — nothing is sent anywhere.</div>
<textarea id=box placeholder="Type naturally here: The quick brown fox jumps over the lazy dog. I am applying for this role because..."></textarea>
</body></html>
"""

# Installed via page.evaluate (NOT an inline <script>): patchright runs evaluate in an isolated world, so a
# main-world inline script's window.__t would be invisible to the read-back. DOM events are shared across
# worlds, so these isolated-world listeners capture the operator's real mouse/scroll/key input.
_RECORD_JS = """() => {
  window.__t = { moves: [], wheels: [], keydowns: [], keyups: [] };
  const t0 = performance.now();
  window.addEventListener('mousemove', e => window.__t.moves.push([performance.now() - t0, e.clientX, e.clientY]), {passive: true});
  window.addEventListener('wheel', e => window.__t.wheels.push([performance.now() - t0, e.deltaY]), {passive: true});
  window.addEventListener('keydown', e => window.__t.keydowns.push([performance.now() - t0, e.code]));
  window.addEventListener('keyup', e => window.__t.keyups.push([performance.now() - t0, e.code]));
}"""


def _segment_moves(moves: list[list[float]]) -> list[dict[str, list[list[float]]]]:
    """Split the continuous [t, x, y] move stream into separate gestures on idle gaps > IDLE_GAP_MS."""
    paths: list[dict[str, list[list[float]]]] = []
    cur: list[list[float]] = []
    prev_t: float | None = None
    for t, x, y in moves:
        if prev_t is not None and t - prev_t > IDLE_GAP_MS and len(cur) >= 3:
            paths.append({"pts": cur})
            cur = []
        cur.append([t, x, y])
        prev_t = t
    if len(cur) >= 3:
        paths.append({"pts": cur})
    return paths


def _segment_wheels(wheels: list[list[float]]) -> list[dict[str, list[list[float]]]]:
    """Split the [t, deltaY] wheel stream into separate scroll gestures on idle gaps > IDLE_GAP_MS."""
    scrolls: list[dict[str, list[list[float]]]] = []
    cur: list[list[float]] = []
    prev_t: float | None = None
    for t, dy in wheels:
        if prev_t is not None and t - prev_t > IDLE_GAP_MS and cur:
            scrolls.append({"events": cur})
            cur = []
        cur.append([t, dy])
        prev_t = t
    if cur:
        scrolls.append({"events": cur})
    return scrolls


def _keystroke_timings(keydowns: list[list[float]], keyups: list[list[float]]) -> dict[str, list[float]]:
    """Derive flight (keydown→next-keydown) and dwell (keydown→matching-keyup) times in ms."""
    flight_ms = [b[0] - a[0] for a, b in zip(keydowns, keydowns[1:], strict=False) if 0 < b[0] - a[0] < 2000]
    dwell_ms: list[float] = []
    for dt, code in keydowns:
        up = next((ut for ut, uc in keyups if uc == code and ut >= dt), None)
        if up is not None and 0 < up - dt < 1000:
            dwell_ms.append(up - dt)
    return {"flight_ms": flight_ms, "dwell_ms": dwell_ms}


async def main() -> None:
    """Open a capture page in real Chrome, record ~SECONDS of human motion, and write it to OUT as JSON."""
    s = Settings()
    OUT.parent.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240 — one-time setup before the browser launches
    print(f"Recording {SECONDS}s of motion — move/scroll/type in the window until the timer hits zero…")
    async with launch_playwright(headful=True, chrome_path=s.chrome_path, no_sandbox=s.chrome_no_sandbox) as page:
        await page.set_content(_CAPTURE_HTML)
        await page.evaluate(_RECORD_JS)  # install listeners in the isolated world we later read from
        for remaining in range(SECONDS, 0, -1):
            await page.evaluate(f"() => {{ const e=document.getElementById('t'); if(e) e.textContent='{remaining}'; }}")
            await asyncio.sleep(1)
        raw = await page.evaluate("() => window.__t") or {}

    mouse_paths = _segment_moves(raw.get("moves", []))
    scrolls = _segment_wheels(raw.get("wheels", []))
    keystrokes = _keystroke_timings(raw.get("keydowns", []), raw.get("keyups", []))
    out = {"version": 1, "mouse_paths": mouse_paths, "scrolls": scrolls, "keystrokes": keystrokes}
    OUT.write_text(json.dumps(out, indent=2))  # noqa: ASYNC240 — one-time write after the browser has closed
    print(
        f"✓ {OUT}  ({len(mouse_paths)} mouse gestures, {len(scrolls)} scrolls, "
        f"{len(keystrokes['flight_ms'])} keystroke flights)\n"
        f"  Enable it:  export JOOBLE_MOTION_TRACES={OUT}"
    )
    if len(mouse_paths) < 5:
        print("! few gestures captured — re-run and keep the mouse moving for the full countdown.")


if __name__ == "__main__":
    asyncio.run(main())
