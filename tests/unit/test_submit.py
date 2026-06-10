from applyme.lever.submit import classify_outcome


def test_thanks_url_is_success():
    out = classify_outcome(final_url="https://jobs.lever.co/aledade/x/thanks", http_status=200, body="<h2>Thank you</h2>")
    assert out.status == "SUCCESS" and out.result_string == "success"


def test_400_with_flagged_field_is_failed(fixture):
    out = classify_outcome(final_url="https://jobs.lever.co/aledade/x/apply", http_status=400, body=fixture("error_400.html"))
    assert out.status == "FAILED"
    assert out.flagged_fields == ["phone"]


def test_400_no_field_flag_is_captcha_blocked():
    body = '<div class="application-form"><p class="error-message">There was an error verifying your application.</p></div>'
    out = classify_outcome(final_url="https://x/apply", http_status=400, body=body)
    assert out.status == "CAPTCHA_BLOCKED" and out.result_string == "captcha blocked"
