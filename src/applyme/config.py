"""Typed settings (fail-fast) + Chrome locate/version pre-flight."""

import shutil
import subprocess
from pathlib import Path

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
    captchasonic_api_key: SecretStr | None = None  # EXPERIMENTAL out-of-band hCaptcha-Enterprise test (REPORT §4)
    llm_api_key: SecretStr | None = None
    llm_model: str = "claude-haiku-4-5-20251001"
    imap_host: str = "imap.gmail.com"
    imap_user: str | None = None
    imap_password: SecretStr | None = None
    submit_mode: SubmitMode = SubmitMode.DRY_RUN
    headful: bool = True
    max_applies: int = 5
    chrome_path: str | None = None
    chrome_no_sandbox: bool = False  # disable Chrome's sandbox (root/containers/CI); auto-falls-back on connect failure
    # Stealth / silent-pass tuning (see docs/REPORT.md §4 — the unattended captcha path)
    user_data_dir: str | None = None  # persistent Chrome profile (carries __cf_bm/cf_clearance across the 5 applies)
    browser_locale: str = "en-US"  # coherence-pinned; do NOT hand-set a UA/Accept-Language (desyncs client-hints)
    browser_timezone: str | None = None  # set to match the egress-IP geo (e.g. "America/New_York") when it drifts
    ipqs_api_key: SecretStr | None = None  # optional: IPQualityScore key for the egress-IP reputation pre-flight
    proxy_server: str | None = (
        None  # optional sticky residential/mobile exit, e.g. "http://host:port" (solve-IP==submit-IP)
    )
    proxy_username: str | None = None
    proxy_password: SecretStr | None = None

    def proxy_config(self) -> dict[str, str] | None:
        """Playwright proxy dict for the persistent context, or None for a direct (home-IP) connection."""
        if not self.proxy_server:
            return None
        cfg: dict[str, str] = {"server": self.proxy_server}
        if self.proxy_username:
            cfg["username"] = self.proxy_username
        if self.proxy_password:
            cfg["password"] = self.proxy_password.get_secret_value()
        return cfg


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
