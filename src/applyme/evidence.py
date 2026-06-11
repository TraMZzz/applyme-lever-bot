"""Per-attempt evidence: redacted HTML snapshot + screenshots + HAR. Capture failures are non-fatal."""

import asyncio
import contextlib
import re
from pathlib import Path

_REDACT = re.compile(r'(name="(?:h-captcha-response|email|phone|eeo\[[^\]]+\])"\s+value=")[^"]*(")', re.I)


def redact_html(html: str) -> str:
    """Blank sensitive values from HTML: hCaptcha token, email, phone, EEO fields."""
    return _REDACT.sub(r"\1[REDACTED]\2", html)


async def capture(tab: object, out_dir: Path, label: str) -> dict[str, str | None]:
    """Save a screenshot and redacted HTML snapshot to out_dir; failures are silently swallowed."""
    out_dir.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
    paths: dict[str, str | None] = {"screenshot": None, "html": None}
    # Each capture is bounded: after Lever's reactive re-render stalls the CDP connection, a DOM-level
    # get_content() can hang. The Page-level screenshot still succeeds, so take it FIRST and bound both
    # so evidence never blocks (or hangs) the apply.
    shot = out_dir / f"{label}.png"
    with contextlib.suppress(Exception):
        async with asyncio.timeout(20):
            # zendriver's save_screenshot defaults format='jpeg' and does NOT infer it from the .png
            # filename — pass it explicitly so .png files actually contain PNG bytes.
            await tab.save_screenshot(str(shot), format="png", full_page=True)  # type: ignore[attr-defined]
            paths["screenshot"] = str(shot)
    with contextlib.suppress(Exception):
        async with asyncio.timeout(15):
            snap = out_dir / f"{label}.html"
            snap.write_text(redact_html(await tab.get_content()))  # type: ignore[attr-defined]
            paths["html"] = str(snap)
    return paths
