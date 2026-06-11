"""Evidence helpers: redact sensitive values from a captured HTML snapshot before it is written."""

import re

_REDACT = re.compile(r'(name="(?:h-captcha-response|email|phone|eeo\[[^\]]+\])"\s+value=")[^"]*(")', re.I)


def redact_html(html: str) -> str:
    """Blank sensitive values from HTML: hCaptcha token, email, phone, EEO fields."""
    return _REDACT.sub(r"\1[REDACTED]\2", html)
