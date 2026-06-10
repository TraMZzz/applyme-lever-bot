"""Set selectedLocation directly (the /searchLocations autocomplete is hCaptcha-gated)."""
import json


def build_selected_location(display: str) -> tuple[str, str]:
    """Return (visible location text, JSON.stringify(locationObject)). Schema validated on leverdemo (see §12)."""
    return display, json.dumps({"name": display})
