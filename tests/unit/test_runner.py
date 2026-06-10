from applyme.models import Vacancy
from applyme.runner import run_one


async def test_exception_becomes_a_classified_result():
    v = Vacancy(company="aledade", posting_id="x", url="https://jobs.lever.co/aledade/x")

    async def boom(vacancy: Vacancy) -> None:
        raise RuntimeError("zendriver hung")

    result = await run_one(v, apply_fn=boom, rng_seed=1)  # type: ignore[arg-type]
    assert result.status in {"RETRYABLE_ERROR", "FAILED"}
    assert result.posting_id == "x" and result.result_string  # always produces a row
