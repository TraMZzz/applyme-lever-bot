"""Submit the form (silent-pass first, single-flight) and classify the outcome."""

from dataclasses import dataclass, field

from selectolax.parser import HTMLParser

from applyme.models import Status


@dataclass
class Outcome:
    """Result of a single form-submit attempt."""

    status: Status
    reason: str = ""
    flagged_fields: list[str] = field(default_factory=list)

    @property
    def result_string(self) -> str:
        """Short human-readable summary string for logging/recording."""
        if self.status == "SUCCESS":
            return "success"
        if self.status == "CAPTCHA_BLOCKED":
            return "captcha blocked"
        if self.status == "FAILED":
            return f"failed:{self.reason}" if self.reason else "failed"
        return self.status.lower()


def _flagged_fields(tree: HTMLParser) -> list[str]:
    """Names of inputs Lever marked invalid on a 400 re-render."""
    names = [n.attributes.get("name", "") for n in tree.css(".field-error [name], [name].error, .error [name]")]
    return list(dict.fromkeys(n for n in names if n))


def _has_error_banner(tree: HTMLParser) -> bool:
    """True if a POPULATED submit-error banner is present.

    The bare `error-message` class is on every Lever page (a CSS rule plus a hidden oversize-resume
    banner), so a substring test false-positives. We require a `p.error-message` with non-empty text
    that is not the resume-oversize banner.
    """
    for n in tree.css("p.error-message, .error-message"):
        cls = n.attributes.get("class") or ""
        if "resume-upload" in cls or "oversize" in cls:
            continue
        if (n.text() or "").strip():
            return True
    return False


def classify_outcome(final_url: str, http_status: int, body: str) -> Outcome:
    """Classify a form-submit response into SUCCESS / FAILED / CAPTCHA_BLOCKED / RETRYABLE_ERROR.

    Success is driven by the reliable signal — a redirect to `/<co>/<id>/thanks` — not by a body
    substring. Failure is read from the re-rendered form: a flagged required field, else a populated
    error banner (same Lever message for bad-captcha and missing-required → reported as
    CAPTCHA_BLOCKED only when no field is flagged).
    """
    if final_url.rstrip("/").endswith("/thanks"):
        return Outcome("SUCCESS")
    tree = HTMLParser(body)
    flagged = _flagged_fields(tree)
    if flagged:
        return Outcome("FAILED", reason=f"MISSING_REQUIRED_FIELD:{flagged[0]}", flagged_fields=flagged)
    if _has_error_banner(tree):
        return Outcome("CAPTCHA_BLOCKED", reason="hcaptcha_unverified")
    if http_status >= 500:
        return Outcome("RETRYABLE_ERROR", reason=f"unexpected_status:{http_status}")
    # Not on /thanks and no parseable error: a re-render we couldn't attribute. Never report success.
    return Outcome("FAILED", reason="no_thanks_redirect")
