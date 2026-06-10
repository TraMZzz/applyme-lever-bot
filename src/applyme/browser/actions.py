"""HumanActions — drive a real zendriver Tab with human-like mouse/keyboard behaviour.

This is the glue `lever.fill.fill_form` calls: it owns the cursor, moves it along a Bézier
path before clicking, types character-by-character with sampled delays, and answers cards by
their visible option text/value (matching how the form parser exposes options).
"""

from __future__ import annotations

import asyncio
import json
import random
from typing import TYPE_CHECKING

import zendriver as zd
from zendriver import cdp

from applyme.browser.human import Point, bezier_path, sample_delay

if TYPE_CHECKING:
    from applyme.models import CardField


def jittered_point(left: float, top: float, width: float, height: float, rng: random.Random) -> Point:
    """Return a point inside the [left, left+width] x [top, top+height] box, biased toward centre.

    The bias keeps clicks away from the very edge (where a 1px miss lands outside the element)
    while still adding realistic jitter. The returned point is guaranteed to lie within bounds.
    """
    # Sample within the inner 60% of each axis, centred on the middle of the box.
    fx = 0.5 + rng.uniform(-0.3, 0.3)
    fy = 0.5 + rng.uniform(-0.3, 0.3)
    return (left + width * fx, top + height * fy)


class HumanActions:
    """Stateful human-input driver bound to one zendriver tab.

    Holds the current cursor position so successive moves form a continuous path, and a seeded
    RNG so a run is reproducible from its seed.
    """

    def __init__(self, tab: zd.Tab, seed: int) -> None:
        """Bind to `tab`; seed the RNG; start the cursor at the top-left corner."""
        self.tab = tab
        self.rng = random.Random(seed)
        self.cursor: Point = (0.0, 0.0)

    async def _move_to(self, x: float, y: float) -> None:
        """Move the cursor to (x, y) along a Bézier path, dispatching mouseMoved per step."""
        for px, py in bezier_path(self.cursor, (x, y), self.rng):
            await self.tab.send(cdp.input_.dispatch_mouse_event(type_="mouseMoved", x=px, y=py))
        self.cursor = (x, y)

    async def _press_release(self, x: float, y: float) -> None:
        """Dispatch a left mousePressed + mouseReleased pair at (x, y)."""
        for action in ("mousePressed", "mouseReleased"):
            await self.tab.send(
                cdp.input_.dispatch_mouse_event(
                    type_=action, x=x, y=y, button=cdp.input_.MouseButton.LEFT, click_count=1
                )
            )

    async def _click_element(self, element: zd.Element) -> None:
        """Scroll an element into view, move to a jittered in-element point, and click it there."""
        await element.scroll_into_view()
        pos = await element.get_position()
        if pos is None:
            await element.click()
            return
        x, y = jittered_point(pos.left, pos.top, pos.width, pos.height, self.rng)
        await self._move_to(x, y)
        await self._press_release(x, y)

    async def click(self, selector: str) -> None:
        """Find the element for `selector` and click it with human-like motion."""
        await self._click_element(await self.tab.select(selector))

    async def type_into(self, tab: zd.Tab, selector: str, text: str) -> None:
        """Focus the field, CLEAR it, then send each character with a per-keystroke delay.

        The clear is essential: Lever's `parseResume` autofills name/email/phone after the resume
        upload, so typing without clearing appends ("Ethan CalderEthan Calder") and the override
        read-back fails verify_overrides. clear_input empties the field first.
        """
        element = await tab.select(selector)
        await self._click_element(element)
        await element.clear_input()
        for char in text:
            await element.send_keys(char)
            await asyncio.sleep(sample_delay("keystroke", self.rng))

    async def answer_card_field(self, tab: zd.Tab, field: CardField, answer: str) -> None:
        """Answer one CardField by its visible option text / value.

        radio/checkbox → click the option whose value or text equals `answer`;
        dropdown → select the <option> whose visible text equals `answer`;
        text/textarea → type the answer into the field.
        """
        if not answer:
            return
        if field.field_type in ("multiple-choice", "multiple-select"):
            base = f'[name="{field.input_name}"]'
            try:
                option = await tab.select(f'{base}[value="{answer}"]')
            except Exception:  # noqa: BLE001 — fall back to matching by visible text
                option = await tab.find(answer, best_match=True)
            await self._click_element(option)
        elif field.field_type == "dropdown":
            await self._select_dropdown(tab, field.input_name, answer)
        else:  # text / textarea
            await self.type_into(tab, f'[name="{field.input_name}"]', answer)

    async def _select_dropdown(self, tab: zd.Tab, name: str, answer: str) -> None:
        """Choose an <option> on a real <select> by visible text or value, dispatching change.

        Element.set_value() maps to CDP DOM.setNodeValue, which does NOT move a <select>'s
        selectedIndex or fire a change event, so the field would submit empty. We match the option
        and set value + dispatch input/change exactly as a user interaction would.
        `getElementsByName` avoids CSS-escaping the bracketed `cards[id][fieldN]` name.
        """
        js = (
            "(() => {"
            f"  const name = {json.dumps(name)}, want = {json.dumps(answer)};"
            "  const s = document.getElementsByName(name)[0];"
            "  if (!s || !s.options) return false;"
            "  const o = [...s.options].find(o => o.text.trim() === want || o.value === want);"
            "  if (!o) return false;"
            "  s.value = o.value;"
            "  s.dispatchEvent(new Event('input', {bubbles: true}));"
            "  s.dispatchEvent(new Event('change', {bubbles: true}));"
            "  return true; })()"
        )
        await tab.evaluate(js)
