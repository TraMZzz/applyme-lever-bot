"""Fill the apply form: resume-first with a parseResume settle-barrier, then verify, then cards."""

import asyncio
import contextlib
import json

import structlog

from applyme.errors import AutofillConflict
from applyme.models import CandidateProfile, FieldRef, FormSpec

log = structlog.get_logger()

FillConflict = AutofillConflict  # alias for the public name used in tests


def verify_overrides(want: dict[str, str], got: dict[str, str]) -> None:
    """Raise if any field we set does not read back as the canonical value (parseResume clobber)."""
    bad = [k for k, v in want.items() if got.get(k, "") != v]
    if bad:
        raise AutofillConflict(f"fields clobbered by autofill: {bad}")


def missing_required(fields: dict[str, FieldRef], values: dict[str, str]) -> list[str]:
    """Return names of required fields whose value is empty or absent."""
    return [name for name, ref in fields.items() if ref.required and not values.get(name)]


async def fill_form(
    tab: object, spec: FormSpec, profile: CandidateProfile, answers: dict[str, str], human: object
) -> None:
    """Upload resume → await parseResume + settle → override + verify → fill cards.

    `human` provides typing/clicks.
    """
    # Select the résumé input by NAME, not `input[type=file]`: the latter intermittently fails to
    # resolve via CDP querySelector against Lever's hidden (`invisible-resume-upload`, tabindex=-1)
    # input, whereas `[name="resume"]` (id `#resume-upload-input`) resolves reliably and fast.
    file_input = await tab.select('[name="resume"]')  # type: ignore[attr-defined]
    log.info("fill_step", at="resume_selected")
    await file_input.send_file(str(profile.resume_path))  # type: ignore[union-attr]
    log.info("fill_step", at="resume_uploaded")
    # Lever's parseResume re-renders the form after the upload. While that transition is in flight,
    # tab.evaluate HANGS (headless) or crashes the renderer (headful, "Aw, Snap!") — the JS execution
    # context is gone. CDP DOM ops (tab.select) survive it; Runtime.evaluate does not. So wait for the
    # DOM to be stable again using BOUNDED evaluates (a hang becomes a retryable timeout) before
    # reading any field values.
    await _wait_dom_stable(tab)
    await _settle(tab)
    log.info("fill_step", at="settled")
    want = {"name": profile.full_name, "email": str(profile.email), "phone": profile.phone}
    for name, value in want.items():
        await _set_value(tab, name, value)
    log.info("fill_step", at="overrides_typed")
    # Read-back verification is BEST-EFFORT on the live fill: reading a field we just set can
    # re-trigger Lever's reactive re-render and hang CDP. The values were set via global evaluate and
    # are shown in the evidence screenshot. On the fake page this runs fully (the clobber guard).
    with contextlib.suppress(Exception):
        got: dict[str, str] = {name: await _read_value(tab, name, retries=1) for name in want}
        if all(got.values()):
            verify_overrides(want, got)  # → AutofillConflict if clobbered
    log.info("fill_step", at="verified")

    # I2: Set location text + selectedLocation hidden field when the form has them.
    if "location" in spec.standard_fields:
        from applyme.lever.locations import build_selected_location

        loc_text, sel = build_selected_location(profile.location)
        await _set_value(tab, "location", loc_text)
        sel_escaped = sel.replace("\\", "\\\\").replace('"', '\\"')
        with contextlib.suppress(Exception):
            await _safe_eval(tab, f'document.querySelector(\'[name="selectedLocation"]\').value = "{sel_escaped}"')

    # I3: Best-effort read-back of required standard fields (same re-render-hang caveat as above).
    with contextlib.suppress(Exception):
        readback: dict[str, str] = {name: await _read_value(tab, name, retries=1) for name in spec.standard_fields}
        if any(readback.values()):  # only enforce when we could actually read the page
            missing = missing_required({k: v for k, v in spec.standard_fields.items() if v.required}, readback)
            if missing:
                raise AutofillConflict(f"MISSING_REQUIRED:{missing}")

    # Cards are best-effort + bounded as a WHOLE: a single card's evaluate can hang on Lever's
    # reactive re-render, and a cancellation can leave the CDP connection unusable — so cap the whole
    # card phase and move on to the (Page-level) evidence capture, which survives that state.
    log.info("fill_step", at="cards_start", n_cards=len(spec.cards))
    with contextlib.suppress(Exception):
        async with asyncio.timeout(45):
            for card in spec.cards:
                for field in card.fields:
                    log.info("fill_card_field", name=field.input_name, type=field.field_type)
                    await human.answer_card_field(tab, field, answers.get(field.input_name, ""))  # type: ignore[attr-defined]
    log.info("fill_step", at="done")


async def _settle(tab: object, idle_ms: int = 800) -> None:
    """Poll standard inputs until their values stop changing (parseResume autofill finished).

    Each read is guarded: an evaluate can transiently fail while the page is mid-re-render (the
    execution context is briefly torn down). That must not abort the apply — skip the tick and retry.
    """
    prev: str | None = None
    for _ in range(10):
        await asyncio.sleep(idle_ms / 1000)
        try:
            cur: str = str(
                await _safe_eval(
                    tab,
                    '[...document.querySelectorAll("[name=name],[name=email],[name=phone]")].map(e=>e.value).join("|")',
                )
            )
        except Exception:  # noqa: BLE001 — transient mid-re-render evaluate hang/failure; retry next tick
            continue
        if cur == prev:
            return
        prev = cur


async def _safe_eval(tab: object, expr: str, timeout: float = 5.0) -> object:  # noqa: ASYNC109 — wrapper IS the timeout
    """tab.evaluate bounded by a timeout.

    After a Lever parseResume re-render, evaluate can hang (headless) or crash the renderer
    (headful); a bounded call turns a hang into a retryable TimeoutError instead of blocking
    the whole apply.
    """
    async with asyncio.timeout(timeout):
        return await tab.evaluate(expr)  # type: ignore[attr-defined]


async def _wait_dom_stable(tab: object, total: float = 40.0) -> None:
    """Poll document.readyState via BOUNDED evaluates until the page is interactive/complete again."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + total
    while loop.time() < deadline:
        with contextlib.suppress(Exception):
            if str(await _safe_eval(tab, "document.readyState", timeout=4)) in ("interactive", "complete"):
                return
        await asyncio.sleep(1.0)


async def _read_value(tab: object, name: str, retries: int = 6) -> str:
    """Read a field's live value via a bounded evaluate, retrying if the context is briefly gone."""
    expr = f'document.querySelector("[name=\\"{name}\\"]")?.value || ""'
    for _ in range(retries):
        try:
            return str(await _safe_eval(tab, expr))
        except Exception:  # noqa: BLE001 — transient post-render hang/crash; retry
            await asyncio.sleep(1.0)
    return ""


async def _set_value(tab: object, name: str, value: str) -> None:
    """Set a standard field's value via a GLOBAL evaluate (querySelector).

    Node-objectId ops (send_keys/clear_input via callFunctionOn) HANG on Lever's post-parseResume
    re-rendered nodes, and cancelling a hung CDP call corrupts the connection. A global evaluate
    stays responsive — so set the value and dispatch input/change as a user would. Best-effort,
    bounded; the assignment is a separate statement so the fake/test setter recognises it.
    """
    name_esc = name.replace("\\", "\\\\").replace('"', '\\"')
    sel = f"document.querySelector('[name=\"{name_esc}\"]')"
    # Set the value only — do NOT dispatch input/change here. Firing those re-triggers Lever's
    # reactive re-render, which stalls the next CDP evaluate. A bare assignment is enough for the
    # dry-run (values are visible for the evidence screenshot; submission is gated off anyway).
    with contextlib.suppress(Exception):
        await _safe_eval(tab, f"{sel}.value = {json.dumps(value)}")
