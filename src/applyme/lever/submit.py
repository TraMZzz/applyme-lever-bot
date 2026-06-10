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


def classify_outcome(final_url: str, http_status: int, body: str) -> Outcome:
    """Classify a form-submit response into SUCCESS / FAILED / CAPTCHA_BLOCKED / RETRYABLE_ERROR."""
    if final_url.rstrip("/").endswith("/thanks") or (
        http_status == 200 and "thank" in body.lower() and "error-message" not in body
    ):
        return Outcome("SUCCESS")
    if http_status == 400 or "error-message" in body:
        tree = HTMLParser(body)
        flagged = [n.attributes.get("name", "") for n in tree.css(".field-error [name], [name].error, .error [name]")]
        flagged = list(dict.fromkeys(f for f in flagged if f))
        if flagged:
            return Outcome("FAILED", reason=f"MISSING_REQUIRED_FIELD:{flagged[0]}", flagged_fields=flagged)
        return Outcome("CAPTCHA_BLOCKED", reason="hcaptcha_unverified")
    return Outcome("RETRYABLE_ERROR", reason=f"unexpected_status:{http_status}")
