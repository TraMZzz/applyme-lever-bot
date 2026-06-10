import asyncio

from applyme.models import ApplyResult, Vacancy
from applyme.runner import run_one


async def test_exception_becomes_a_classified_result():
    v = Vacancy(company="aledade", posting_id="x", url="https://jobs.lever.co/aledade/x")

    async def boom(vacancy: Vacancy) -> None:
        raise RuntimeError("zendriver hung")

    result = await run_one(v, apply_fn=boom, rng_seed=1)  # type: ignore[arg-type]
    assert result.status in {"RETRYABLE_ERROR", "FAILED"}
    assert result.posting_id == "x" and result.result_string  # always produces a row


async def test_per_apply_timeout_yields_retryable_error():
    v = Vacancy(company="aledade", posting_id="x", url="https://jobs.lever.co/aledade/x")

    async def slow(vacancy: Vacancy) -> ApplyResult:
        await asyncio.sleep(10)
        raise AssertionError("should have timed out")

    result = await run_one(v, apply_fn=slow, rng_seed=1, per_apply_timeout=0.05)
    assert result.status == "RETRYABLE_ERROR"
    assert "per_apply_timeout" in result.reason
