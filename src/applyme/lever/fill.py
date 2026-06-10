"""Fill the apply form: resume-first with a parseResume settle-barrier, then verify, then cards."""

import asyncio

from applyme.errors import AutofillConflict
from applyme.models import CandidateProfile, FieldRef, FormSpec

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
    file_input = await tab.select("input[type=file]")  # type: ignore[attr-defined]
    async with tab.expect_response(r".*/parseResume.*"):  # type: ignore[attr-defined]
        await file_input.send_file(str(profile.resume_path))  # type: ignore[union-attr]
    await _settle(tab)
    want = {"name": profile.full_name, "email": str(profile.email), "phone": profile.phone}
    for name, value in want.items():
        await human.type_into(tab, f'[name="{name}"]', value)  # type: ignore[attr-defined]
    got: dict[str, str] = {
        name: str(
            await tab.evaluate(  # type: ignore[attr-defined]
                f'document.querySelector("[name=\\"{name}\\"]").value'
            )
        )
        for name in want
    }
    verify_overrides(want, got)  # → AutofillConflict if clobbered

    # I2: Set location text + selectedLocation hidden field when the form has them.
    if "location" in spec.standard_fields:
        from applyme.lever.locations import build_selected_location

        loc_text, sel = build_selected_location(profile.location)
        await human.type_into(tab, '[name="location"]', loc_text)  # type: ignore[attr-defined]
        sel_escaped = sel.replace("\\", "\\\\").replace('"', '\\"')
        await tab.evaluate(  # type: ignore[attr-defined]
            f'document.querySelector(\'[name="selectedLocation"]\').value = "{sel_escaped}"'
        )

    # I3: Read back all required standard fields and raise if any are still empty.
    readback: dict[str, str] = {}
    for name in spec.standard_fields:
        readback[name] = str(
            await tab.evaluate(  # type: ignore[attr-defined]
                f'document.querySelector("[name=\\"{name}\\"]").value'
            )
        )
    missing = missing_required(
        {k: v for k, v in spec.standard_fields.items() if v.required},
        readback,
    )
    if missing:
        raise AutofillConflict(f"MISSING_REQUIRED:{missing}")

    for card in spec.cards:
        for field in card.fields:
            await human.answer_card_field(tab, field, answers.get(field.input_name, ""))  # type: ignore[attr-defined]


async def _settle(tab: object, idle_ms: int = 800) -> None:
    """Poll standard inputs until their values stop changing (parseResume autofill finished)."""
    prev: str | None = None
    for _ in range(10):
        await asyncio.sleep(idle_ms / 1000)
        cur: str = str(
            await tab.evaluate(  # type: ignore[attr-defined]
                '[...document.querySelectorAll("[name=name],[name=email],[name=phone]")].map(e=>e.value).join("|")'
            )
        )
        if cur == prev:
            return
        prev = cur
