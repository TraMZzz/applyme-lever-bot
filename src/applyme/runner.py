"""Orchestrate vacancies sequentially; every vacancy yields exactly one ApplyResult."""
import asyncio
import json
import random
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

import structlog

from applyme.browser.human import sample_delay
from applyme.errors import PermanentError
from applyme.models import ApplyResult, Vacancy

log = structlog.get_logger()


def _now() -> datetime:
    return datetime.now(UTC)


async def run_one(
    v: Vacancy,
    apply_fn: Callable[[Vacancy], Awaitable[ApplyResult]],
    rng_seed: int,
) -> ApplyResult:
    """Run apply_fn for a single vacancy, converting any exception to a classified ApplyResult.

    PermanentError → FAILED; everything else → RETRYABLE_ERROR.
    One vacancy can never abort the batch.
    """
    started = _now()
    try:
        return await apply_fn(v)
    except PermanentError as e:
        status, reason = "FAILED", str(e)
    except Exception as e:  # noqa: BLE001 — nothing escapes as a crash
        status, reason = "RETRYABLE_ERROR", str(e)
    return ApplyResult(
        posting_url=str(v.url),
        company=v.company,
        posting_id=v.posting_id,
        status=status,
        reason=reason,
        rng_seed=rng_seed,
        started_at=started,
        finished_at=_now(),
    )


async def run_all(
    vacancies: list[Vacancy],
    apply_fn: Callable[[Vacancy], Awaitable[ApplyResult]],
    out: Path,
    seed: int = 0,
) -> list[ApplyResult]:
    """Apply to all vacancies sequentially with human-scale inter-apply delays.

    Writes results to `out` as JSON with an extra `result_string` field per row.
    """
    rng = random.Random(seed)
    results: list[ApplyResult] = []
    for i, v in enumerate(vacancies):
        if i:
            await asyncio.sleep(sample_delay("inter_apply", rng))  # human-scale gap between applies
        results.append(await run_one(v, apply_fn, rng_seed=rng.randint(1, 2**31)))
    out.parent.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
    out.write_text(  # noqa: ASYNC240
        json.dumps(
            [{**r.model_dump(mode="json"), "result_string": r.result_string} for r in results],
            indent=2,
            default=str,
        )
    )
    return results
