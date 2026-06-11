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


def test_missing_fields_default_to_fail_closed():
    # An empty/garbage response must not pass (fraud_score defaults to 100).
    ok, _ = evaluate_ip({})
    assert not ok
