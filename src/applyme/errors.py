"""Exception hierarchy: RetryableError is retried by tenacity; PermanentError is not."""


class ApplyError(Exception):
    """Base for all bot errors."""


class RetryableError(ApplyError):
    """Transient — safe to retry (network, Cloudflare managed challenge, solver timeout)."""


class PermanentError(ApplyError):
    """Do not retry (bad config/key, unmapped schema, oversize payload, autofill conflict)."""


class NetworkError(RetryableError): ...


class CloudflareChallenge(RetryableError): ...


class SolverTimeout(RetryableError): ...


class SolverAuthError(PermanentError): ...


class SolverUnavailable(PermanentError):
    """The provider does not (or no longer) solves this captcha type, or it can't be solved out-of-band.

    Distinct from SolverTimeout (transient) and SolverAuthError (bad key): the solve is *structurally*
    impossible — CapSolver delisted hCaptcha in 2026, and Lever's invisible Enterprise hCaptcha needs a
    fresh per-challenge ``rqdata`` (+ IP-matched proxy) that an out-of-band solve can't supply. Permanent:
    never retried, and a clear signal to record ``captcha_blocked`` rather than inject an empty token.
    """


class SchemaUnmappedError(PermanentError): ...


class PayloadTooLargeError(PermanentError): ...


class AutofillConflict(PermanentError): ...


class WebDriverLeak(PermanentError):
    """navigator.webdriver was truthy — abort rather than apply with a detectable signal."""
