#!/usr/bin/env python3
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Motion smoke — prove the human-motion engine actually drives a real browser:

    uv run python scripts/check_motion.py
    JOOBLE_MOTION_TRACES=data/motion/human_traces.json uv run python scripts/check_motion.py

Needs nothing but Chrome (no data/, no API keys). Launches the real browser via patchright, loads a tiny page
that records every DOM mouse/scroll/key event, then drives the PRODUCTION motion engine
(`Settings.motion_engine()` → `path_to`/`scroll`/`keystroke_delay`) and verifies the browser actually observed
human-shaped motion: many non-instant mouse samples, a scroll, and per-character keystrokes with real timing.

This is the end-to-end check the unit tests can't do (they drive a stub page). It reports which motion source is
live — `recorded` (real traces from scripts/record_motion.py) or the synthetic Bézier fallback — so you can
confirm `JOOBLE_MOTION_TRACES` is wired before measuring the silent-pass rate on a leverdemo submit. Exits
non-zero if the browser saw no/!human motion, so it wires into a pre-run gate. Reads the same env as the bot.
"""

import asyncio
import random
import statistics

from applyme.browser.human import jittered_point
from applyme.browser.motion import MotionEngine
from applyme.config import Settings

_BODY_HTML = '<!doctype html><meta charset=utf-8><body><textarea id=box style="width:60ch;height:6em"></textarea></body>'

# Installed via page.evaluate (NOT an inline <script>): patchright runs evaluate in an isolated world, so a
# main-world inline script's window.__ev would be invisible to the read-back. The DOM is shared across worlds,
# so these isolated-world listeners still observe the real mouse/scroll/key events the engine dispatches.
_SETUP_JS = """() => {
  window.__ev = { moves: [], wheels: [], keys: [] };
  const t0 = performance.now();
  window.addEventListener('mousemove', e => window.__ev.moves.push([performance.now() - t0, e.clientX, e.clientY]), {passive: true});
  window.addEventListener('wheel', e => window.__ev.wheels.push([performance.now() - t0, e.deltaY]), {passive: true});
  window.addEventListener('keydown', e => window.__ev.keys.push([performance.now() - t0, e.code]));
}"""

_TYPE_TEXT = "The quick brown fox jumps over the lazy dog."


async def drive_and_capture(page: object, motion: MotionEngine, rng: object) -> dict[str, list[list[float]]]:
    """Drive the motion engine (mouse drift + scroll + typing) on the capture page; return the observed events."""
    await page.set_content(_BODY_HTML)  # type: ignore[attr-defined]
    await page.evaluate(_SETUP_JS)  # type: ignore[attr-defined]
    dims = await page.evaluate("() => [window.innerWidth, window.innerHeight]")  # type: ignore[attr-defined]
    w, h = (dims[0] or 1280), (dims[1] or 800)
    cur = (w * 0.5, h * 0.5)
    for _ in range(4):
        dest = jittered_point(0, 0, w, h, rng)  # type: ignore[arg-type]
        for x, y, sleep_s in motion.path_to(cur, dest, rng):  # type: ignore[arg-type]
            await page.mouse.move(x, y)  # type: ignore[attr-defined]
            await asyncio.sleep(sleep_s)
        cur = dest
    for sleep_s, delta_y in motion.scroll(rng):  # type: ignore[arg-type]
        await asyncio.sleep(sleep_s)
        await page.mouse.wheel(0, delta_y)  # type: ignore[attr-defined]
    await page.click("#box")  # type: ignore[attr-defined]
    for char in _TYPE_TEXT:
        await page.keyboard.type(char)  # type: ignore[attr-defined]
        await asyncio.sleep(motion.keystroke_delay(rng))  # type: ignore[arg-type]
    ev = await page.evaluate("() => window.__ev")  # type: ignore[attr-defined]
    return ev or {"moves": [], "wheels": [], "keys": []}


def summarize(ev: dict[str, list[list[float]]] | None) -> dict[str, float]:
    """Reduce the captured event stream to the figures the smoke checks (counts + mean inter-move delay)."""
    ev = ev or {}
    moves = ev.get("moves", [])
    dts = [(b[0] - a[0]) / 1000.0 for a, b in zip(moves, moves[1:], strict=False) if b[0] >= a[0]]
    return {
        "moves": len(moves),
        "wheels": len(ev.get("wheels", [])),
        "keys": len(ev.get("keys", [])),
        "mean_dt_s": statistics.fmean(dts) if dts else 0.0,
    }


def check(summary: dict[str, float]) -> list[str]:
    """Return a list of failed smoke assertions (empty ⇒ the browser saw real, human-shaped motion)."""
    failures: list[str] = []
    if summary["moves"] < 10:
        failures.append(f"too few mouse samples observed ({summary['moves']} < 10) — engine didn't drive the page")
    if summary["wheels"] < 1:
        failures.append("no scroll event observed")
    if summary["keys"] < 30:
        failures.append(f"too few keystrokes observed ({summary['keys']} < 30)")
    if not 0.001 <= summary["mean_dt_s"] <= 0.12:
        failures.append(f"mean inter-move delay {summary['mean_dt_s']:.4f}s is outside the human range (1-120ms)")
    return failures


async def main() -> None:
    """Launch real Chrome, drive the motion engine on a capture page, and report whether it saw human motion."""
    from applyme.browser.pw_engine import launch_playwright

    s = Settings()
    motion = s.motion_engine()
    print(
        f"motion source : {motion.source}  (recorded paths={len(motion.mouse_paths)}, flights={len(motion.flights_s)})"
    )
    print(f"mode          : headful={s.headful} no_sandbox={s.chrome_no_sandbox}")
    async with launch_playwright(headful=s.headful, chrome_path=s.chrome_path, no_sandbox=s.chrome_no_sandbox) as page:
        ev = await drive_and_capture(page, motion, random.Random(0))

    summary = summarize(ev)
    print(
        f"observed      : {summary['moves']} mouse samples, {summary['wheels']} scrolls, {summary['keys']} keys, "
        f"mean move gap {summary['mean_dt_s'] * 1000:.1f}ms"
    )
    failures = check(summary)
    if failures:
        print("FAIL — the browser did not observe human-shaped motion:")
        for f in failures:
            print(f"  - {f}")
        raise SystemExit(1)
    print(f"OK | motion engine drives real Chrome | source={motion.source}")


if __name__ == "__main__":
    asyncio.run(main())
