"""CapSolver via async REST (the official SDK is stale: no async + a 60s cap)."""

import asyncio

import httpx

from applyme.captcha._const import SITEKEY
from applyme.errors import SolverAuthError, SolverTimeout, SolverUnavailable


def extract_token(result: dict) -> str:  # type: ignore[type-arg]
    """Extract the gRecaptchaResponse token from a CapSolver task result."""
    return result["solution"]["gRecaptchaResponse"]  # type: ignore[no-any-return]


def error_for(code: str, description: str) -> Exception:
    """Map a CapSolver ``createTask`` error into our exception hierarchy.

    CapSolver delisted hCaptcha in 2026 (verified: the hCaptcha doc page 404s; it is absent from the
    api-support / createTask / SDK surfaces). For an hCaptcha task it returns ``ERROR_INVALID_TASK_DATA``
    with the description ``"This service is not supported."`` — a permanent, provider-delisted condition
    (``SolverUnavailable``), NOT a malformed-payload or bad-key error. Everything else is a
    config/auth fault (``SolverAuthError``); neither is retried (both are PermanentError).
    """
    if "not supported" in description.lower() or code == "ERROR_TASK_NOT_SUPPORTED":
        return SolverUnavailable(f"CapSolver no longer solves hCaptcha ({code}: {description})")
    return SolverAuthError(f"{code}: {description}")


async def solve(*, page_url: str, ua: str, rqdata: str | None, key: str, max_wait: float = 90.0) -> str:
    """Submit an hCaptcha task to CapSolver and poll until ready or timed out."""
    task: dict[str, object] = {
        "type": "HCaptchaTaskProxyless",
        "websiteURL": page_url,
        "websiteKey": SITEKEY,
        "isInvisible": True,
        "userAgent": ua,
    }
    if rqdata:
        task["enterprisePayload"] = {"rqdata": rqdata}
    async with httpx.AsyncClient(base_url="https://api.capsolver.com", timeout=30) as h:
        r = (await h.post("/createTask", json={"clientKey": key, "task": task})).json()
        if r.get("errorId"):
            raise error_for(r.get("errorCode") or "", r.get("errorDescription") or "")
        task_id = r["taskId"]
        loop = asyncio.get_running_loop()
        end = loop.time() + max_wait
        while loop.time() < end:
            await asyncio.sleep(3)
            res = (await h.post("/getTaskResult", json={"clientKey": key, "taskId": task_id})).json()
            if res.get("status") == "ready":
                return extract_token(res)  # type: ignore[no-any-return]
        raise SolverTimeout("CapSolver hCaptcha timed out")
