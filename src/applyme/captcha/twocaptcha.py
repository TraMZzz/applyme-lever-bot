"""2Captcha fallback (hybrid human+ML)."""
from applyme.captcha._const import SITEKEY
from applyme.errors import SolverTimeout


def extract_token(result: dict) -> str:  # type: ignore[type-arg]
    """Extract the token from a 2Captcha result dict."""
    return result["code"]  # type: ignore[no-any-return]


async def solve(*, page_url: str, ua: str, rqdata: str | None, key: str) -> str:
    """Solve hCaptcha via the 2Captcha async SDK (install name: 2captcha-python)."""
    from twocaptcha import AsyncTwoCaptcha  # install name: 2captcha-python  # noqa: PLC0415

    solver = AsyncTwoCaptcha(key, defaultTimeout=110, pollingInterval=5)
    try:
        res: dict[str, str] = await solver.hcaptcha(  # type: ignore[reportUnknownMemberType]
            sitekey=SITEKEY,
            url=page_url,
            invisible=1,
            data=rqdata,
            userAgent=ua,
        )
    except Exception as e:  # noqa: BLE001 — normalise vendor errors
        raise SolverTimeout(str(e)) from e
    return extract_token(res)
