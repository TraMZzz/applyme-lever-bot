import pytest

from applyme.app import apply_to_vacancy_with_page
from applyme.models import Vacancy

pytestmark = pytest.mark.integration


async def test_apply_flow_classifies_success_and_fills_form(fake_page_factory, profile_fixture):
    """The real composition must classify SUCCESS AND have actually filled the form.

    This asserts against the honest fake's recorded state, so it fails if app.py ever stops
    routing through fill_form (the dead-code regression this test guards).
    """
    page = fake_page_factory(apply_html_fixture="apply_aledade.html", final_url=".../thanks", status=200)
    v = Vacancy(company="leverdemo", posting_id="x", url="https://jobs.lever.co/leverdemo/x")

    result = await apply_to_vacancy_with_page(v, profile_fixture, page, submit_mode="sandbox")

    assert result.status == "SUCCESS"
    # The form was actually filled: resume uploaded and standard fields typed with profile values.
    assert page.uploaded_file is not None
    assert page.typed.get("name") == profile_fixture.full_name
    assert page.typed.get("email") == str(profile_fixture.email)
    assert page.typed.get("phone") == profile_fixture.phone
    # Submit was triggered through the human cursor.
    assert page.clicks  # verify_overrides readback ran → fields were verified
