"""Exception hierarchy: RetryableError marks transient failures; PermanentError is not retried."""


class ApplyError(Exception):
    """Base for all bot errors."""


class RetryableError(ApplyError):
    """Transient — safe to retry (network blip, solver timeout); classified RETRYABLE_ERROR by the runner."""


class PermanentError(ApplyError):
    """Do not retry (bad config/key, no usable solver, a detectable automation signal)."""


class SolverTimeout(RetryableError):
    """A captcha solver did not return a token within the deadline."""


class SolverAuthError(PermanentError):
    """A captcha solver rejected the request (bad key / config)."""


class SolverUnavailable(PermanentError):
    """The provider does not (or no longer) solves this captcha type, or it can't be solved out-of-band.

    Distinct from SolverTimeout (transient) and SolverAuthError (bad key): the solve is *structurally*
    impossible — CapSolver delisted hCaptcha in 2026, and Lever's invisible Enterprise hCaptcha needs a
    fresh per-challenge ``rqdata`` (+ IP-matched proxy) that an out-of-band solve can't supply. Permanent:
    never retried, and a clear signal to record ``captcha_blocked`` rather than inject an empty token.
    """


class WebDriverLeak(PermanentError):
    """navigator.webdriver was truthy — abort rather than apply with a detectable signal."""
