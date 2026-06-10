from applyme.captcha import base, capsolver, twocaptcha
from applyme.errors import SolverTimeout


def test_capsolver_extracts_grecaptcha_key():
    assert capsolver.extract_token({"solution": {"gRecaptchaResponse": "TOK"}}) == "TOK"


def test_twocaptcha_extracts_code_key():
    assert twocaptcha.extract_token({"code": "TOK2"}) == "TOK2"


async def test_failover_uses_fallback_when_primary_times_out(monkeypatch):
    async def boom(**kw):
        raise SolverTimeout("slow")

    async def ok(**kw):
        return "FALLBACK"

    monkeypatch.setattr(capsolver, "solve", boom)
    monkeypatch.setattr(twocaptcha, "solve", ok)
    token, vendor = await base.solve_hcaptcha(
        page_url="u", ua="UA", rqdata=None, capsolver_key="k", twocaptcha_key="k2"
    )
    assert token == "FALLBACK"
    assert vendor == "twocaptcha"
