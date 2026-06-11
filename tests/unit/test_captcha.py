import pytest

from applyme.captcha import base, capsolver, twocaptcha
from applyme.errors import SolverAuthError, SolverTimeout, SolverUnavailable


def test_capsolver_extracts_grecaptcha_key():
    assert capsolver.extract_token({"solution": {"gRecaptchaResponse": "TOK"}}) == "TOK"


def test_twocaptcha_extracts_code_key():
    assert twocaptcha.extract_token({"code": "TOK2"}) == "TOK2"


def test_capsolver_error_for_maps_not_supported_to_unavailable():
    # CapSolver delisted hCaptcha in 2026: ERROR_INVALID_TASK_DATA / "This service is not supported."
    # must be a permanent, provider-delisted condition — not retried, not mistaken for a bad key.
    err = capsolver.error_for("ERROR_INVALID_TASK_DATA", "This service is not supported.")
    assert isinstance(err, SolverUnavailable)
    assert isinstance(capsolver.error_for("ERROR_TASK_NOT_SUPPORTED", ""), SolverUnavailable)


def test_capsolver_error_for_maps_other_errors_to_auth():
    err = capsolver.error_for("ERROR_KEY_DENIED_ACCESS", "key is wrong")
    assert isinstance(err, SolverAuthError) and not isinstance(err, SolverUnavailable)


async def test_solve_hcaptcha_fails_closed_without_rqdata():
    # Lever's invisible Enterprise hCaptcha needs a fresh per-challenge rqdata; with none captured the
    # solver must refuse rather than fire a doomed proxyless request / inject an empty token.
    with pytest.raises(SolverUnavailable):
        await base.solve_hcaptcha(page_url="u", ua="UA", rqdata=None, capsolver_key="k", twocaptcha_key="k2")


async def test_failover_uses_fallback_when_primary_times_out(monkeypatch):
    async def boom(**kw):
        raise SolverTimeout("slow")

    async def ok(**kw):
        return "FALLBACK"

    monkeypatch.setattr(capsolver, "solve", boom)
    monkeypatch.setattr(twocaptcha, "solve", ok)
    # rqdata present → the solver path runs (and fails CapSolver over to 2Captcha as designed).
    token, vendor = await base.solve_hcaptcha(
        page_url="u", ua="UA", rqdata="RQ", capsolver_key="k", twocaptcha_key="k2"
    )
    assert token == "FALLBACK"
    assert vendor == "twocaptcha"
