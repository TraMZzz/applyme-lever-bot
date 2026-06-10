"""CapSolver via async REST (the official SDK is stale: no async + a 60s cap)."""
import asyncio

import httpx

from applyme.captcha._const import SITEKEY
from applyme.errors import SolverAuthError, SolverTimeout


def extract_token(result: dict) -> str:  # type: ignore[type-arg]
    """Extract the gRecaptchaResponse token from a CapSolver task result."""
    return result["solution"]["gRecaptchaResponse"]  # type: ignore[no-any-return]


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
            raise SolverAuthError(f"{r.get('errorCode')}: {r.get('errorDescription')}")
        task_id = r["taskId"]
        loop = asyncio.get_running_loop()
        end = loop.time() + max_wait
        while loop.time() < end:
            await asyncio.sleep(3)
            res = (await h.post("/getTaskResult", json={"clientKey": key, "taskId": task_id})).json()
            if res.get("status") == "ready":
                return extract_token(res)  # type: ignore[no-any-return]
        raise SolverTimeout("CapSolver hCaptcha timed out")
