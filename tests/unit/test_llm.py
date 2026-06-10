from applyme.answers.llm import validate_choice


def test_validate_choice_rejects_out_of_options():
    assert validate_choice("Yes", ["Yes", "No"]) == "Yes"
    assert validate_choice("Maybe", ["Yes", "No"]) is None  # not an allowed option
    assert validate_choice("  yes ", ["Yes", "No"]) == "Yes"  # normalised


def test_validate_choice_tolerates_verbose_reply():
    # The live failure: the model answered correctly but appended reasoning. Must still resolve.
    assert validate_choice("No\n\n**Reasoning:** the candidate has no such experience.", ["Yes", "No"]) == "No"
    assert validate_choice("Yes, the candidate qualifies.", ["Yes", "No"]) == "Yes"


def test_validate_choice_terse_reply_against_long_options():
    # A bare "No" must map to the long opt-out option (most specific match wins).
    opts = ["Yes, I want to opt in.", "No, I do not want to opt in."]
    assert validate_choice("No", opts) == "No, I do not want to opt in."


def test_validate_choice_picks_named_option():
    assert validate_choice("California", ["Alabama", "California", "Colorado"]) == "California"
