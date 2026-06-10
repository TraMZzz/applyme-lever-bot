"""Optional LLM fallback for unmapped required questions; output validated ∈ options.

The model id is NOT hardcoded here — it comes from `config.Settings.llm_model`
(env `JOOBLE_LLM_MODEL`, default `claude-haiku-4-5-20251001`) and is passed in by the caller.
"""

import re

from anthropic import AsyncAnthropic


def validate_choice(answer: str, options: list[str]) -> str | None:
    """Return the option `answer` resolves to, tolerating a verbose model reply, else None.

    A constrained model usually returns just the option text, but it may add punctuation or a
    "No\\n\\n**Reasoning:** …" tail. We resolve in increasing-leniency order and, when several
    options could match (e.g. bare "No" against both "No" and "No, I do not opt in"), prefer the
    LONGEST option so the most specific choice wins. Comparison is case-insensitive; the original
    option casing is returned.
    """
    norm = answer.strip().lower()
    if not norm:
        return None
    opts = [(o, o.strip().lower()) for o in options]

    # 1. Exact match (the constrained-output happy path).
    for original, low in opts:
        if low == norm:
            return original

    # 2. First non-empty line equals an option (model added a blank line + reasoning below).
    first_line = next((ln.strip() for ln in norm.splitlines() if ln.strip()), "")
    for original, low in opts:
        if low == first_line:
            return original

    # 3. Bidirectional prefix: answer starts with an option (verbose answer), or an option starts
    #    with the answer (terse answer vs a long option). Longest match wins.
    pref = [original for original, low in opts if norm.startswith(low) or low.startswith(norm)]
    if pref:
        return max(pref, key=len)

    # 4. An option appears as a standalone word in the answer. Longest match wins.
    word = [original for original, low in opts if re.search(rf"\b{re.escape(low)}\b", norm)]
    if word:
        return max(word, key=len)

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
    if options:
        system = (
            "You answer one job-application screening question for the given candidate. "
            "Reply with EXACTLY one of the allowed options, copied verbatim. "
            "Output only that option text — no punctuation, quotes, reasoning, or extra words."
        )
        instruction = f"Allowed options (copy exactly one, verbatim): {options}"
        max_tokens = 64
    else:
        system = "You answer one job-application question for the given candidate, concisely, in at most two sentences."
        instruction = "Answer in <=2 sentences."
        max_tokens = 200
    msg = await client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": f"Candidate:\n{profile_summary}\n\nQuestion: {question}\n{instruction}"}],
    )
    raw: str = getattr(msg.content[0], "text", "")
    text = raw.strip()
    return validate_choice(text, options) if options else text
