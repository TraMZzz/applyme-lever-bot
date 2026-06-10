import pytest

from applyme.app import apply_to_vacancy_with_page
from applyme.models import Vacancy

pytestmark = pytest.mark.integration


async def test_apply_flow_classifies_success(fake_page_factory, profile_fixture):
    page = fake_page_factory(apply_html_fixture="apply_aledade.html", final_url=".../thanks", status=200)
    v = Vacancy(company="leverdemo", posting_id="x", url="https://jobs.lever.co/leverdemo/x")
    result = await apply_to_vacancy_with_page(v, profile_fixture, page, submit_mode="sandbox")
    assert result.status == "SUCCESS"
