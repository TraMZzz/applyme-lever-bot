from applyme.answers.llm import validate_choice


def test_validate_choice_rejects_out_of_options():
    assert validate_choice("Yes", ["Yes", "No"]) == "Yes"
    assert validate_choice("Maybe", ["Yes", "No"]) is None  # not an allowed option
    assert validate_choice("  yes ", ["Yes", "No"]) == "Yes"  # normalised
