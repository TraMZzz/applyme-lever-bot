from applyme.evidence import redact_html


def test_redact_blanks_token_and_pii():
    html = (
        '<input name="h-captcha-response" value="LIVE_TOKEN">'
        '<input name="email" value="ethan@applyme.site">'
        '<input name="phone" value="9175552244">'
    )
    out = redact_html(html)
    assert "LIVE_TOKEN" not in out and "ethan@applyme.site" not in out and "9175552244" not in out
    assert 'name="email"' in out  # structure preserved
