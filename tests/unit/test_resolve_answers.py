"""resolve_answers fails closed when the LLM fallback stalls — a slow SDK call must not eat the budget."""

import asyncio
from pathlib import Path

from applyme import app
from applyme.config import Settings
from applyme.models import CandidateProfile, Card, CardField, FormSpec


def _profile() -> CandidateProfile:
    return CandidateProfile(
        full_name="E",
        email="e@x.com",
        phone="1",
        location="New York, NY, United States",
        city="New York",
        state="NY",
        country="US",
        work_authorized=True,
        requires_sponsorship=False,
        willing_to_relocate=False,
        expected_salary=140000,
        resume_path=Path("r.pdf"),
    )


def _freetext_spec() -> FormSpec:
    # A non-sensitive free-text card the rules engine leaves unmapped → the LLM fallback path.
    field = CardField(
        field_index=0,
        field_type="textarea",
        text="Describe your favourite project",
        required=True,
        options=[],
        input_name="cards[c][field0]",
    )
    return FormSpec(
        standard_fields={},
        cards=[Card(card_id="c", fields=[field])],
        sitekey="e33f87f8-88ec-4e1a-9a13-df9bbb1d8120",
        account_id="leverdemo",
        posting_id="x",
    )


async def test_llm_fallback_times_out_fail_closed(monkeypatch):
    settings = Settings(llm_api_key="sk-test", llm_timeout_s=0.05)  # type: ignore[arg-type]

    async def slow_answer(*_a: object, **_k: object) -> str:
        await asyncio.sleep(5)  # never returns within the 0.05s bound
        raise AssertionError("should have been cancelled by the timeout")

    monkeypatch.setattr("applyme.answers.llm.answer_question", slow_answer)

    loop = asyncio.get_running_loop()
    t0 = loop.time()
    answers, unmapped = await app.resolve_answers(_profile(), _freetext_spec(), settings)
    elapsed = loop.time() - t0

    # Bounded: the stalled call is cancelled near llm_timeout_s, not 5s.
    assert elapsed < 1.0
    # Fail-closed: the field stays unmapped and is never invented.
    assert "cards[c][field0]" not in answers
    assert "cards[c][field0]" in unmapped
