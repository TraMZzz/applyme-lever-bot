"""Typed settings (fail-fast) + Chrome locate/version pre-flight."""

import shutil
import subprocess
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from applyme.models import SubmitMode

_CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
]


class ChromeNotFoundError(RuntimeError):
    """Chrome could not be located — set JOOBLE_CHROME_PATH."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JOOBLE_", env_file=".env", extra="ignore", frozen=True)
    capsolver_api_key: SecretStr | None = None
    twocaptcha_api_key: SecretStr | None = None
    llm_api_key: SecretStr | None = None
    llm_model: str = "claude-haiku-4-5-20251001"
    imap_host: str = "imap.gmail.com"
    imap_user: str | None = None
    imap_password: SecretStr | None = None
    submit_mode: SubmitMode = SubmitMode.DRY_RUN
    headful: bool = True
    max_applies: int = 5
    # Browser engine: "patchright" (Playwright, auto-waits through Lever's parseResume re-render —
    # the working default) or "zendriver" (raw-CDP, stealthiest but hangs on the re-render).
    engine: Literal["patchright", "zendriver"] = "patchright"
    chrome_path: str | None = None
    chrome_no_sandbox: bool = False  # disable Chrome's sandbox (root/containers/CI); auto-falls-back on connect failure


def find_chrome(override: str | None = None) -> str:
    """Return a usable Chrome binary path or raise ChromeNotFoundError."""
    candidates = [override] if override else [shutil.which("google-chrome"), *_CHROME_CANDIDATES]
    for c in candidates:
        if c and Path(c).exists():
            return c
    raise ChromeNotFoundError("Chrome not found; set JOOBLE_CHROME_PATH to the binary.")


def chrome_version(path: str) -> str:
    """Best-effort '--version' string (used for the supported-range log line)."""
    try:
        return subprocess.run([path, "--version"], capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"
