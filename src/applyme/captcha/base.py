"""hCaptcha solving with CapSolver→2Captcha failover. Token shapes are normalised per-vendor."""

from applyme.captcha import capsolver, twocaptcha
from applyme.captcha._const import SITEKEY as SITEKEY  # re-export: base.SITEKEY is the public name
from applyme.errors import PermanentError, RetryableError


async def solve_hcaptcha(
    page_url: str,
    ua: str,
    rqdata: str | None,
    capsolver_key: str | None,
    twocaptcha_key: str | None,
) -> str:
    """Solve hCaptcha with CapSolver; fall over to 2Captcha on any RetryableError/PermanentError."""
    if capsolver_key:
        try:
            return await capsolver.solve(page_url=page_url, ua=ua, rqdata=rqdata, key=capsolver_key)
        except (RetryableError, PermanentError):
            if not twocaptcha_key:
                raise
    return await twocaptcha.solve(page_url=page_url, ua=ua, rqdata=rqdata, key=twocaptcha_key)  # type: ignore[arg-type]
