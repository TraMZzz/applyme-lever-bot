import pytest

from applyme.config import ChromeNotFoundError, Settings, find_chrome


def test_settings_loads_from_env(monkeypatch):
    monkeypatch.setenv("JOOBLE_CAPSOLVER_API_KEY", "secret123")
    monkeypatch.setenv("JOOBLE_IMAP_PASSWORD", "pw")
    s = Settings(_env_file=None)  # isolate from a developer .env so defaults are deterministic
    assert s.capsolver_api_key.get_secret_value() == "secret123"
    assert s.submit_mode == "dry-run"  # safe default
    assert "secret123" not in repr(s)  # SecretStr masks


def test_find_chrome_raises_when_missing(monkeypatch):
    monkeypatch.setenv("JOOBLE_CHROME_PATH", "/no/such/chrome")
    with pytest.raises(ChromeNotFoundError):
        find_chrome("/no/such/chrome")
