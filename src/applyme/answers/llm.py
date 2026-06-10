"""Optional LLM fallback for unmapped required questions; output validated ∈ options.

The model id is NOT hardcoded here — it comes from `config.Settings.llm_model`
(env `JOOBLE_LLM_MODEL`, default `claude-haiku-4-5-20251001`) and is passed in by the caller.
"""

from anthropic import AsyncAnthropic


def validate_choice(answer: str, options: list[str]) -> str | None:
    """Return the matching option if `answer` normalises to one of `options`, else None.

    Normalisation strips leading/trailing whitespace and folds to lowercase for comparison,
    but returns the original option casing on match.
    """
    norm = answer.strip().lower()
    for o in options:
        if o.strip().lower() == norm:
            return o
    return None


async def answer_question(
    api_key: str, profile_summary: str, question: str, options: list[str], model: str
) -> str | None:
    """Ask the LLM to answer a card question, then validate the response is within allowed options.

    Args:
        api_key: Anthropic API key.
        profile_summary: A short textual description of the candidate profile.
        question: The card question text.
        options: Allowed option strings; empty list means free-text is acceptable.
        model: Anthropic model id (from Settings.llm_model).

    Returns:
        A validated option string, a free-text answer (if no options), or None on mismatch.
    """
    client = AsyncAnthropic(api_key=api_key)
    opts = f"Choose exactly one of: {options}" if options else "Answer in <=2 sentences."
    msg = await client.messages.create(
        model=model,
        max_tokens=200,
        messages=[{"role": "user", "content": f"Candidate:\n{profile_summary}\n\nQuestion: {question}\n{opts}"}],
    )
    raw: str = getattr(msg.content[0], "text", "")
    text = raw.strip()
    return validate_choice(text, options) if options else text
