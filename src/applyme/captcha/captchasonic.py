"""CaptchaSonic hCaptcha solver — async REST (CapSolver-compatible createTask/getTaskResult).

**EXPERIMENTAL / falsification test** (docs/REPORT.md §4). CaptchaSonic is one of the few solvers still
advertising hCaptcha Enterprise (`enterprisePayload`/`rqdata`, `isInvisible`). It is wired so the claim can
be **tested**, not asserted: for Lever's *invisible* Enterprise hCaptcha the token IS a passive risk score,
and an out-of-band token (minted in CaptchaSonic's session, not ours) is expected to be `siteverify`-rejected
— we already saw our *own* best-context token rejected, and a remote one can only score equal-or-worse. The
solve-IP must equal the submit-IP, so a fair test routes BOTH the browser and this call through the same proxy.

Verified API (2026-06-11): base `https://api.captchasonic.com`, `clientKey` + `task`, token at
`solution.gRecaptchaResponse`.
"""

import asyncio

import httpx

from applyme.captcha._const import SITEKEY
from applyme.errors import SolverAuthError, SolverTimeout


def _proxy_fields(proxy: dict[str, str]) -> dict[str, object]:
    """Map a Playwright proxy dict ({server, username?, password?}) → CaptchaSonic proxy fields."""
    server = proxy["server"]  # e.g. "http://host:port"
    scheme, _, hostport = server.rpartition("://")
    host, _, port = hostport.partition(":")
    fields: dict[str, object] = {
        "proxyType": (scheme or "http").lower(),
        "proxyAddress": host,
        "proxyPort": int(port) if port.isdigit() else 8080,
    }
    if proxy.get("username"):
        fields["proxyLogin"] = proxy["username"]
    if proxy.get("password"):
        fields["proxyPassword"] = proxy["password"]
    return fields


async def solve(
    *,
    page_url: str,
    ua: str,
    rqdata: str | None,
    key: str,
    proxy: dict[str, str] | None = None,
    max_wait: float = 120.0,
) -> str:
    """Submit an hCaptcha task to CaptchaSonic and poll until ready or timed out.

    Uses `HCaptchaTask` (proxied — solve-IP == submit-IP) when a proxy is given, else `HCaptchaTaskProxyless`.
    """
    task: dict[str, object] = {
        "type": "HCaptchaTask" if proxy else "HCaptchaTaskProxyless",
        "websiteURL": page_url,
        "websiteKey": SITEKEY,
        "isInvisible": True,
        "userAgent": ua,
    }
    if rqdata:
        task["enterprisePayload"] = {"rqdata": rqdata}
    if proxy:
        task.update(_proxy_fields(proxy))
    async with httpx.AsyncClient(base_url="https://api.captchasonic.com", timeout=30) as h:
        r = (await h.post("/createTask", json={"clientKey": key, "task": task})).json()
        if r.get("errorId"):
            raise SolverAuthError(f"{r.get('errorCode')}: {r.get('errorDescription')}")
        task_id = r["taskId"]
        loop = asyncio.get_running_loop()
        end = loop.time() + max_wait
        while loop.time() < end:
            await asyncio.sleep(3)
            res = (await h.post("/getTaskResult", json={"clientKey": key, "taskId": task_id})).json()
            if res.get("errorId"):
                raise SolverAuthError(f"{res.get('errorCode')}: {res.get('errorDescription')}")
            if res.get("status") == "ready":
                return res["solution"]["gRecaptchaResponse"]  # type: ignore[no-any-return]
        raise SolverTimeout("CaptchaSonic hCaptcha timed out")
