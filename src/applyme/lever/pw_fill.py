"""Patchright-native fill: resume → override standard fields → cards.

Uses Playwright's auto-waiting `fill`/`check`/`select_option`, which survive Lever's parseResume
re-render (raw-CDP `evaluate` hangs there). Human behaviour is preserved on the standard fields:
a Bézier mouse move to the field + per-character typing with log-normal delays.
"""

import asyncio
import contextlib
import difflib
import random

import structlog
from patchright.async_api import Page

from applyme.answers.llm import validate_choice
from applyme.browser.human import jittered_point
from applyme.browser.motion import MotionEngine
from applyme.models import CandidateProfile, FormSpec

log = structlog.get_logger()

Point = tuple[float, float]


def _css(value: str) -> str:
    """Escape a value for use inside a double-quoted CSS attribute selector."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _closest_option(answer: str, values: list[str]) -> str | None:
    """Closest DOM option to `answer` by string similarity — a bounded last resort.

    Used only after `validate_choice` finds no tolerant match: it bridges near-identical strings (the
    parsed option vs the rendered radio `value` differing by whitespace/encoding) so a produced answer
    still ticks its option. The 0.72 cutoff is deliberately high — a genuine paraphrase or a wrong
    gradation stays unmatched (the field then fail-closes honestly rather than tick the wrong choice).
    """
    if not answer or not values:
        return None

    def norm(s: str) -> str:
        return " ".join(s.lower().split())

    a = norm(answer)
    best = max(values, key=lambda v: difflib.SequenceMatcher(None, a, norm(v)).ratio())
    return best if difflib.SequenceMatcher(None, a, norm(best)).ratio() >= 0.72 else None


def _link_for_label(label: str, links: dict[str, str]) -> str | None:
    """Match a Lever `urls[<label>]` field to a profile link by keyword (case-insensitive).

    Lever names link fields `urls[LinkedIn]`, `urls[GitHub]`, `urls[Please provide your LinkedIn]`, …; the
    bracket label varies per posting, so we keyword-match it against the profile's link keys both ways.
    """
    low = label.lower()
    for key, url in links.items():
        k = key.lower()
        if k and url and (k in low or low in k):
            return url
    return None


async def _human_move(page: Page, rng: random.Random, motion: MotionEngine, cursor: Point, x: float, y: float) -> Point:
    """Move the mouse to (x, y) along a recorded-human (or synthetic-fallback) path with real per-sample timing."""
    for px, py, sleep_s in motion.path_to(cursor, (x, y), rng):
        await page.mouse.move(px, py)
        await asyncio.sleep(sleep_s)
    return (x, y)


async def _human_fill(
    page: Page, rng: random.Random, motion: MotionEngine, cursor: Point, selector: str, text: str
) -> Point:
    """Human-move to a field, click a jittered in-element point, clear it, and type with human keystroke timing."""
    loc = page.locator(selector).first
    await loc.wait_for(state="visible", timeout=15000)
    box = await loc.bounding_box()
    if box:
        tx, ty = jittered_point(box["x"], box["y"], box["width"], box["height"], rng)
        cursor = await _human_move(page, rng, motion, cursor, tx, ty)
        await page.mouse.click(tx, ty)
    await loc.fill("")  # clear any parseResume autofill (auto-waits through the re-render)
    for char in text:
        await page.keyboard.type(char)
        await asyncio.sleep(motion.keystroke_delay(rng))
    return cursor


async def _fill_url_fields(
    page: Page, rng: random.Random, motion: MotionEngine, cursor: Point, links: dict[str, str]
) -> Point:
    """Fill `urls[<label>]` link fields (LinkedIn/GitHub/…) the profile has a matching link for.

    Some postings mark a link field (e.g. LinkedIn) `required`; leaving it empty makes the browser's
    native validation silently block the submit. Best-effort and bounded — unmatched fields are skipped.
    """
    loc = page.locator('input[name^="urls["]')
    for i in range(await loc.count()):
        name = await loc.nth(i).get_attribute("name") or ""
        if "[" not in name or "]" not in name:
            continue
        url = _link_for_label(name[name.index("[") + 1 : name.rindex("]")], links)
        if url:
            with contextlib.suppress(Exception):
                cursor = await _human_fill(page, rng, motion, cursor, f'[name="{_css(name)}"]', url)
    return cursor


async def pw_fill_form(
    page: Page,
    spec: FormSpec,
    profile: CandidateProfile,
    answers: dict[str, str],
    seed: int,
    motion: MotionEngine | None = None,
) -> None:
    """Upload resume → human-override name/email/phone(/location) → link fields → answer cards."""
    rng = random.Random(seed)
    motion = motion or MotionEngine.synthetic()
    cursor: Point = (0.0, 0.0)

    await page.set_input_files('[name="resume"]', str(profile.resume_path))
    log.info("pw_fill", at="resume_uploaded")
    with contextlib.suppress(Exception):
        await page.wait_for_load_state("networkidle", timeout=8000)

    want = {"name": profile.full_name, "email": str(profile.email), "phone": profile.phone}
    for name, value in want.items():
        cursor = await _human_fill(page, rng, motion, cursor, f'[name="{name}"]', value)
    log.info("pw_fill", at="standard_done")

    location_values: tuple[str, str] | None = None
    if "location" in spec.standard_fields:
        from applyme.lever.locations import build_selected_location

        loc_text, sel = build_selected_location(profile.location)
        location_values = (loc_text, sel)
        cursor = await _human_fill(page, rng, motion, cursor, '[name="location"]', loc_text)

    if profile.links:
        cursor = await _fill_url_fields(page, rng, motion, cursor, profile.links)

    for card in spec.cards:
        for field in card.fields:
            answer = answers.get(field.input_name, "")
            if not answer:
                continue
            await _answer_card(page, field.input_name, field.field_type, answer)

    # Re-assert location LAST: Lever's typeahead clears the visible input on blur when no suggestion was
    # picked, so moving to the next field wipes it. Set the visible value + hidden selectedLocation after
    # all other field interactions (nothing blurs it afterward) so the required field isn't seen empty.
    if location_values is not None:
        loc_text, sel = location_values
        with contextlib.suppress(Exception):
            await page.eval_on_selector('[name="location"]', "(e, v) => { e.value = v; }", loc_text)
            await page.eval_on_selector('[name="selectedLocation"]', "(e, v) => { e.value = v; }", sel)
    log.info("pw_fill", at="cards_done")


async def _answer_card(page: Page, name: str, field_type: str, answer: str) -> None:
    """Answer one card field with Playwright's auto-waiting primitives (best-effort, bounded)."""
    base = f'[name="{_css(name)}"]'
    with contextlib.suppress(Exception):
        if field_type in ("multiple-choice", "multiple-select"):
            # The radio/checkbox `value` is the full option TEXT (often a long sentence with smart quotes).
            # Building a `[value="…"]` selector from it is fragile (special chars / encoding) and fails
            # silently. Instead read the option values, tolerant-match the answer in Python (validate_choice
            # folds smart quotes / verbose replies), and check the matched element BY INDEX.
            loc = page.locator(base)
            values = [await loc.nth(i).get_attribute("value") or "" for i in range(await loc.count())]
            chosen = validate_choice(answer, values) or _closest_option(answer, values)
            if chosen and chosen in values:
                await loc.nth(values.index(chosen)).check(timeout=10000, force=True)
            else:
                log.warning("card_answer_unmatched", field=name, answer=answer[:60], options=len(values))
        elif field_type == "dropdown":
            await page.select_option(base, label=answer, timeout=10000)
        else:  # text / textarea
            await page.fill(base, answer, timeout=10000)
