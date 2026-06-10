from pathlib import Path

from applyme.answers.rules import is_sensitive, map_answers
from applyme.models import CandidateProfile, Card, CardField


def _profile():
    return CandidateProfile(
        full_name="E",
        email="e@x.com",
        phone="1",
        location="New York, NY, United States",
        city="New York",
        state="NY",
        country="US",
        work_authorized=True,
        requires_sponsorship=False,
        willing_to_relocate=False,
        expected_salary=140000,
        resume_path=Path("r.pdf"),
    )


def _card(text, options, ftype="multiple-choice", required=True):
    return Card(
        card_id="c",
        fields=[
            CardField(
                field_index=0,
                field_type=ftype,
                text=text,
                required=required,
                options=options,
                input_name="cards[c][field0]",
            )
        ],
    )


def test_work_authorization_yes_and_sponsorship_no():
    ans, unmapped = map_answers(_profile(), [_card("Are you legally authorized to work in the US?", ["Yes", "No"])])
    assert ans["cards[c][field0]"] == "Yes" and not unmapped

    ans, _ = map_answers(_profile(), [_card("Do you require sponsorship?", ["Yes", "No"])])
    assert ans["cards[c][field0]"] == "No"


def test_salary_text_and_unmapped_freetext():
    ans, _ = map_answers(_profile(), [_card("Desired salary?", [], ftype="text")])
    assert ans["cards[c][field0]"] == "140000"
    ans, unmapped = map_answers(_profile(), [_card("Describe your favourite project", [], ftype="textarea")])
    assert "cards[c][field0]" in unmapped


def test_consent_not_hijacked_by_state_substring():
    # "...all statements..." contains the substring "state"; the consent branch must win, and the
    # state branch must not fire on a non-dropdown field.
    card = _card(
        'By clicking "Submit Application" I certify that all statements made are true.',
        ["Submit Application"],
        ftype="multiple-select",
    )
    ans, unmapped = map_answers(_profile(), [card])
    assert ans["cards[c][field0]"] == "Submit Application" and not unmapped


def test_state_dropdown_resolves_to_full_name():
    card = _card(
        "Please list the state you will be located in.",
        ["Alabama", "California", "New York", "Texas"],
        ftype="dropdown",
    )
    ans, _ = map_answers(_profile(), [card])
    assert ans["cards[c][field0]"] == "New York"


def test_sponsorship_phrased_with_legally_is_not_work_auth():
    # A sponsorship question that also contains "legally" must resolve via the sponsorship branch.
    card = _card("Will you now or in future legally require visa sponsorship?", ["Yes", "No"])
    ans, _ = map_answers(_profile(), [card])
    assert ans["cards[c][field0]"] == "No"  # requires_sponsorship is False


def test_is_sensitive_flags_eligibility_and_eeo_not_skills():
    assert is_sensitive("Do you now or in the future require visa sponsorship?")
    assert is_sensitive("Are you a US citizen subject to ITAR/export-control rules?")
    assert is_sensitive("Please self-identify your gender (EEO).")
    assert is_sensitive("Have you ever been convicted of a felony?")
    assert not is_sensitive("Do you have six (6) years of machine learning experience?")
    assert not is_sensitive("How did you hear about this role?")
