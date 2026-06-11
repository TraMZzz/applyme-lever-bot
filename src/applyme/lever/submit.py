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


# Lever ships a hidden oversize-resume banner on EVERY apply page; its class is the bare `error-message`
# and its identity is in the TEXT, so a class-only skip misses it and every non-/thanks page false-reads
# as an error. Match these phrases to skip it. (Verified in a real leverdemo final.html, 2026-06-11.)
_BENIGN_BANNER_MARKERS = ("exceeds the maximum upload size", "oversize")


def _has_error_banner(tree: HTMLParser) -> bool:
    """True if a POPULATED, real submit-error banner is present.

    The bare `error-message` class is on every Lever page (a CSS rule plus a hidden oversize-resume
    banner), so presence alone false-positives. We require a `p.error-message` with non-empty text that
    is neither classed as the resume banner NOR carrying the always-present oversize-upload message.
    """
    for n in tree.css("p.error-message, .error-message"):
        cls = n.attributes.get("class") or ""
        txt = (n.text() or "").strip()
        if not txt:
            continue
        low = txt.lower()
        if "resume-upload" in cls or "oversize" in cls or any(m in low for m in _BENIGN_BANNER_MARKERS):
            continue
        return True
    return False


def classify_outcome(final_url: str, http_status: int, body: str) -> Outcome:
    """Classify a form-submit response into SUCCESS / FAILED / CAPTCHA_BLOCKED / RETRYABLE_ERROR.

    Success is driven by the reliable signal — the post-submit navigation — not by a body substring.
    Two accepted success redirects: a real posting lands on `/<co>/<id>/thanks`; a tenant without a
    thanks page (e.g. `leverdemo`) bounces off the apply form to a Lever URL bearing a minted
    application id (`…?LeverAppId=<uuid>`), which Lever issues only after the POST is accepted. Failure
    is read from the re-rendered form: a flagged required field, else a populated error banner (same
    Lever message for bad-captcha and missing-required → CAPTCHA_BLOCKED only when no field is flagged).
    """
    low = final_url.lower()
    if low.rstrip("/").endswith("/thanks"):
        return Outcome("SUCCESS")
    # leverdemo has no /<co>/<id>/thanks; a successful submit redirects off the form to e.g.
    # www.lever.co/hp-b?LeverAppId=<uuid>. The id is minted only after the POST is accepted (captcha
    # passed + required fields OK), so this redirect is an authoritative success — verified live 2026-06-11.
    if "lever.co" in low and "leverappid=" in low:
        return Outcome("SUCCESS")
    # Lever redirects a repeat application for the same email+posting to /<co>/<id>/already-received —
    # i.e. it WAS submitted (on an earlier run); a duplicate, not a failure. Verified live 2026-06-11.
    if "/already-received" in low:
        return Outcome("DUPLICATE_SUSPECTED", reason="already_applied")
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
