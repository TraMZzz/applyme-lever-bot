"""hCaptcha solving with CapSolver→2Captcha failover. Token shapes are normalised per-vendor."""

from applyme.captcha import capsolver, twocaptcha
from applyme.captcha._const import SITEKEY as SITEKEY  # re-export: base.SITEKEY is the public name
from applyme.errors import PermanentError, RetryableError, SolverUnavailable


async def solve_hcaptcha(
    page_url: str,
    ua: str,
    rqdata: str | None,
    capsolver_key: str | None,
    twocaptcha_key: str | None,
) -> tuple[str, str]:
    """Solve hCaptcha with CapSolver; fall over to 2Captcha on any RetryableError/PermanentError.

    Fails closed (``SolverUnavailable``) when ``rqdata`` is missing: Lever uses invisible **Enterprise**
    hCaptcha (``secure-api.js``), whose token is graded against a fresh per-challenge ``rqdata`` blob and
    an IP-matched solve — an out-of-band, proxyless solve without it mints a token Lever's ``siteverify``
    rejects (verified 2026-06, docs/REPORT.md §4). We capture no ``rqdata`` and run no solver proxy, so we
    refuse the doomed request rather than fire it / inject an empty token. The CapSolver→2Captcha path
    below is kept for a future in-session rqdata-capture tier (REPORT §4 "Option C"), not the live flow.

    Returns:
        A (token, vendor) tuple where vendor is ``"capsolver"`` or ``"twocaptcha"`` — whichever
        service actually returned the token.

    Raises:
        SolverUnavailable: when ``rqdata`` is missing (no out-of-band solve can succeed for Lever).
    """
    if not rqdata:
        raise SolverUnavailable(
            "Lever invisible Enterprise hCaptcha needs a fresh per-challenge rqdata (+ IP-matched proxy); "
            "none captured, so no out-of-band solve can mint a Lever-accepted token. See docs/REPORT.md §4."
        )
    if capsolver_key:
        try:
            token = await capsolver.solve(page_url=page_url, ua=ua, rqdata=rqdata, key=capsolver_key)
            return token, "capsolver"
        except (RetryableError, PermanentError):
            if not twocaptcha_key:
                raise
    token = await twocaptcha.solve(page_url=page_url, ua=ua, rqdata=rqdata, key=twocaptcha_key)  # type: ignore[arg-type]
    return token, "twocaptcha"
