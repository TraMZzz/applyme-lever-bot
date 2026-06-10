from applyme.lever.form import parse_form_html


def test_parse_standard_fields_and_cards(fixture):
    spec = parse_form_html(fixture("apply_aledade.html"),
                           posting_url="https://jobs.lever.co/aledade/x/apply")
    assert spec.sitekey == "e33f87f8-88ec-4e1a-9a13-df9bbb1d8120"
    assert "name" in spec.standard_fields and spec.standard_fields["phone"].required
    assert len(spec.cards) >= 1
    field = spec.cards[0].fields[0]
    assert field.input_name.startswith("cards[") or field.input_name.startswith("surveysResponses[")
    assert field.options  # choice questions carry option TEXT
