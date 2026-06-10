import json

from applyme.profile_loader import load_profile, load_vacancies


def test_load_profile_maps_json(tmp_path):
    (tmp_path / "resume.pdf").write_bytes(b"%PDF-1.4 fake")
    p = tmp_path / "profile.json"
    p.write_text(
        json.dumps(
            {
                "full_name": "Ethan Calder",
                "email": "ethan@applyme.site",
                "phone": "9175552244",
                "location": "New York, NY, United States",
                "city": "New York",
                "state": "NY",
                "country": "United States",
                "work_authorized": True,
                "requires_sponsorship": False,
                "willing_to_relocate": False,
                "expected_salary": 140000,
            }
        )
    )
    prof = load_profile(p, resume_path=tmp_path / "resume.pdf")
    assert prof.full_name == "Ethan Calder"
    assert prof.expected_salary == 140000


def test_load_vacancies_parses_lever_urls(tmp_path):
    f = tmp_path / "vac.txt"
    f.write_text("https://jobs.lever.co/aledade/6fd40837\nhttps://jobs.lever.co/raptv/57dfc4b3\n")
    vacs = load_vacancies(f)
    assert vacs[0].company == "aledade" and vacs[0].posting_id == "6fd40837"
    assert len(vacs) == 2
