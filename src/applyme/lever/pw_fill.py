"""Patchright-native fill: resume → override standard fields → cards.

Uses Playwright's auto-waiting `fill`/`check`/`select_option`, which survive Lever's parseResume
re-render (raw-CDP `evaluate` hangs there). Human behaviour is preserved on the standard fields:
a Bézier mouse move to the field + per-character typing with log-normal delays.
"""

import asyncio
import contextlib
import random

import structlog
from patchright.async_api import Page

from applyme.browser.human import bezier_path, jittered_point, sample_delay
from applyme.models import CandidateProfile, FormSpec

log = structlog.get_logger()

Point = tuple[float, float]


def _css(value: str) -> str:
    """Escape a value for use inside a double-quoted CSS attribute selector."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


async def _human_move(page: Page, rng: random.Random, cursor: Point, x: float, y: float) -> Point:
    """Move the mouse along a Bézier path (curved, non-constant velocity) to (x, y)."""
    for px, py in bezier_path(cursor, (x, y), rng):
        await page.mouse.move(px, py)
    return (x, y)


async def _human_fill(page: Page, rng: random.Random, cursor: Point, selector: str, text: str) -> Point:
    """Bézier-move to a field, click a jittered in-element point, clear it, and type per-character."""
    loc = page.locator(selector).first
    await loc.wait_for(state="visible", timeout=15000)
    box = await loc.bounding_box()
    if box:
        tx, ty = jittered_point(box["x"], box["y"], box["width"], box["height"], rng)
        cursor = await _human_move(page, rng, cursor, tx, ty)
        await page.mouse.click(tx, ty)
    await loc.fill("")  # clear any parseResume autofill (auto-waits through the re-render)
    for char in text:
        await page.keyboard.type(char)
        await asyncio.sleep(sample_delay("keystroke", rng))
    return cursor


async def pw_fill_form(
    page: Page, spec: FormSpec, profile: CandidateProfile, answers: dict[str, str], seed: int
) -> None:
    """Upload resume → human-override name/email/phone(/location) → answer cards."""
    rng = random.Random(seed)
    cursor: Point = (0.0, 0.0)

    await page.set_input_files('[name="resume"]', str(profile.resume_path))
    log.info("pw_fill", at="resume_uploaded")
    with contextlib.suppress(Exception):
        await page.wait_for_load_state("networkidle", timeout=8000)

    want = {"name": profile.full_name, "email": str(profile.email), "phone": profile.phone}
    for name, value in want.items():
        cursor = await _human_fill(page, rng, cursor, f'[name="{name}"]', value)
    log.info("pw_fill", at="standard_done")

    if "location" in spec.standard_fields:
        from applyme.lever.locations import build_selected_location

        loc_text, sel = build_selected_location(profile.location)
        cursor = await _human_fill(page, rng, cursor, '[name="location"]', loc_text)
        with contextlib.suppress(Exception):
            await page.eval_on_selector('[name="selectedLocation"]', "(e, v) => { e.value = v; }", sel)

    for card in spec.cards:
        for field in card.fields:
            answer = answers.get(field.input_name, "")
            if not answer:
                continue
            await _answer_card(page, field.input_name, field.field_type, answer)
    log.info("pw_fill", at="cards_done")


async def _answer_card(page: Page, name: str, field_type: str, answer: str) -> None:
    """Answer one card field with Playwright's auto-waiting primitives (best-effort, bounded)."""
    base = f'[name="{_css(name)}"]'
    with contextlib.suppress(Exception):
        if field_type in ("multiple-choice", "multiple-select"):
            await page.check(f'{base}[value="{_css(answer)}"]', timeout=10000, force=True)
        elif field_type == "dropdown":
            await page.select_option(base, label=answer, timeout=10000)
        else:  # text / textarea
            await page.fill(base, answer, timeout=10000)
