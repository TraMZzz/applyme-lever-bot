import json

from applyme.lever.locations import build_selected_location


def test_build_selected_location_is_json_with_name():
    loc, sel = build_selected_location("New York, NY, United States")
    assert loc == "New York, NY, United States"
    assert json.loads(sel)["name"] == "New York, NY, United States"
