from applyme.lever.submit import classify_outcome


def test_thanks_url_is_success():
    out = classify_outcome(
        final_url="https://jobs.lever.co/aledade/x/thanks", http_status=200, body="<h2>Thank you</h2>"
    )
    assert out.status == "SUCCESS" and out.result_string == "success"


def test_400_with_flagged_field_is_failed(fixture):
    out = classify_outcome(
        final_url="https://jobs.lever.co/aledade/x/apply", http_status=400, body=fixture("error_400.html")
    )
    assert out.status == "FAILED"
    assert out.flagged_fields == ["phone"]


def test_400_no_field_flag_is_captcha_blocked():
    body = '<div class="application-form"><p class="error-message">There was an error verifying your application.</p></div>'
    out = classify_outcome(final_url="https://x/apply", http_status=400, body=body)
    assert out.status == "CAPTCHA_BLOCKED" and out.result_string == "captcha blocked"


def test_hidden_oversize_banner_is_not_a_false_error():
    # Every real Lever page ships a hidden oversize-resume <p class="error-message ...">; it must NOT
    # be read as a submit error (the old bare-substring check false-positived as CAPTCHA_BLOCKED).
    body = '<form><p class="error-message resume-upload-oversize">Your resume is too large.</p></form>'
    out = classify_outcome(final_url="https://x/apply", http_status=200, body=body)
    assert out.status == "FAILED" and out.reason == "no_thanks_redirect"


def test_real_leverdemo_oversize_banner_text_only_is_not_captcha():
    # The ACTUAL leverdemo banner (captured 2026-06-11): bare `error-message` class, identity in the
    # TEXT. The class-only skip missed it, so every non-/thanks page mis-read as CAPTCHA_BLOCKED. This
    # is what masked the submit-button bug as a "captcha block" — guard it by text.
    body = '<form><p class="error-message">File exceeds the maximum upload size of 100MB. Please try a smaller size.</p></form>'
    out = classify_outcome(final_url="https://jobs.lever.co/leverdemo/x/apply", http_status=200, body=body)
    assert out.status == "FAILED" and out.reason == "no_thanks_redirect"


def test_leverdemo_appid_redirect_is_success():
    # leverdemo has no /<co>/<id>/thanks page; a successful submit redirects off the form to
    # www.lever.co/hp-b?LeverAppId=<uuid>. Lever mints that id only after the POST is accepted (captcha
    # passed + required fields OK), so it's an authoritative success. Verified live 2026-06-11.
    out = classify_outcome(
        final_url="https://www.lever.co/hp-b?LeverAppId=9d738247-c28b-4091-87a6-ec4355daf08b",
        http_status=200,
        body="<html><head><title>Lever</title></head><body>marketing</body></html>",
    )
    assert out.status == "SUCCESS" and out.result_string == "success"


def test_lever_marketing_without_appid_is_not_success():
    # Bare www.lever.co with NO application id is not a submit success — must not false-positive.
    out = classify_outcome(final_url="https://www.lever.co", http_status=200, body="<html></html>")
    assert out.status == "FAILED" and out.reason == "no_thanks_redirect"


def test_already_received_is_duplicate():
    # Lever redirects a repeat application for the same email+posting to /<co>/<id>/already-received —
    # it WAS submitted (a duplicate), not a failure. Verified live 2026-06-11.
    out = classify_outcome(
        final_url="https://jobs.lever.co/aledade/6fd40837/already-received?ms=1781114665084",
        http_status=200,
        body="<html></html>",
    )
    assert out.status == "DUPLICATE_SUSPECTED" and out.reason == "already_applied"


def test_genuine_captcha_error_still_classifies_blocked():
    # A REAL error banner (different text) must still be detected as CAPTCHA_BLOCKED even when the
    # always-present oversize banner sits beside it.
    body = (
        '<form><p class="error-message">File exceeds the maximum upload size of 100MB.</p>'
        '<p class="error-message">There was an error verifying your application.</p></form>'
    )
    out = classify_outcome(final_url="https://jobs.lever.co/leverdemo/x/apply", http_status=200, body=body)
    assert out.status == "CAPTCHA_BLOCKED" and out.reason == "hcaptcha_unverified"
