from applyme.browser.preflight import evaluate_ip


def test_clean_residential_passes():
    ok, reason = evaluate_ip({"fraud_score": 0, "connection_type": "Residential", "proxy": False, "vpn": False})
    assert ok and "clean" in reason


def test_clean_mobile_passes():
    ok, _ = evaluate_ip({"fraud_score": 12, "connection_type": "Mobile"})
    assert ok


def test_high_fraud_score_fails():
    ok, reason = evaluate_ip({"fraud_score": 80, "connection_type": "Residential"})
    assert not ok and "fraud_score" in reason


def test_proxy_or_vpn_flag_fails():
    ok, reason = evaluate_ip({"fraud_score": 5, "connection_type": "Residential", "vpn": True})
    assert not ok and "vpn" in reason


def test_datacenter_connection_fails():
    ok, reason = evaluate_ip({"fraud_score": 5, "connection_type": "Data Center"})
    assert not ok and "connection_type" in reason


def test_ipqs_error_is_advisory_not_a_dirty_ip():
    # IPQS signals a bad key/plan with success:false + a message — surface it and PROCEED (don't mistake
    # an API error for a score-100 IP). This was the live bug: an unscored IP read as "fraud_score=100".
    ok, reason = evaluate_ip({"success": False, "message": "Invalid or expired API key."})
    assert ok and "Invalid or expired API key" in reason


def test_no_fraud_score_is_advisory():
    ok, reason = evaluate_ip({})  # empty/garbage ⇒ unscoreable ⇒ advisory, not a hard block
    assert ok and "unscored" in reason
