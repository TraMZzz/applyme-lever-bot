from pathlib import Path

from applyme.answers.rules import map_answers
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
