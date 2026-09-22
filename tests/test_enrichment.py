import sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).parent.parent))

from scraper import PageResult
from gemini import GeminiError
import enrichment


def test_clean_treats_empty_and_null_words_as_none():
    assert enrichment._clean("") is None
    assert enrichment._clean("  ") is None
    assert enrichment._clean("null") is None
    assert enrichment._clean("N/A") is None
    assert enrichment._clean("Maria Gomez") == "Maria Gomez"


def test_clean_list_drops_blank_entries():
    assert enrichment._clean_list(["Plumbing repair", "  ", "", "Water heaters"]) == ["Plumbing repair", "Water heaters"]
    assert enrichment._clean_list(None) == []


def test_clean_decision_makers_drops_entries_without_a_name():
    raw = [{"name": "Maria Gomez", "title": "Owner"}, {"name": "", "title": "Manager"}]
    assert enrichment._clean_decision_makers(raw) == [{"name": "Maria Gomez", "title": "Owner"}]


def test_build_context_skips_failed_pages():
    pages = [
        PageResult(path="/", url="https://x.com", ok=True, text="Welcome to our site"),
        PageResult(path="/contact", url="https://x.com/contact", ok=False, error="timeout"),
    ]
    context = enrichment._build_context(pages)
    assert "Welcome to our site" in context
    assert "timeout" not in context


def test_extract_contact_info_raises_when_no_content():
    pages = [PageResult(path="/", url="https://x.com", ok=False, error="dns error")]
    try:
        enrichment.extract_contact_info(pages)
        assert False, "should have raised"
    except GeminiError as e:
        assert "no renderable page content" in str(e)


def test_run_enrichment_job_fails_honestly_with_no_website():
    with patch("enrichment.db") as mock_db, patch("enrichment.jobs") as mock_jobs:
        mock_db.get_lead.return_value = type("L", (), {"website": None})()
        enrichment.run_enrichment_job("job1", 1)
        last_call = mock_jobs.update_job.call_args
        assert last_call.kwargs.get("status") == "failed"
        assert "no website" in last_call.kwargs.get("error", "").lower()


def test_run_enrichment_job_captures_the_full_field_set():
    fake_lead = type("L", (), {"website": "https://example.com", "id": 1})()
    fake_pages = [PageResult(path="/", url="https://example.com", ok=True, text="lots of real content")]

    with patch("enrichment.db") as mock_db, \
         patch("enrichment.jobs") as mock_jobs, \
         patch("enrichment.scraper") as mock_scraper, \
         patch("enrichment.generate_structured") as mock_gemini:
        mock_db.get_lead.return_value = fake_lead
        mock_scraper.fetch_site_context_sync.return_value = fake_pages
        mock_gemini.return_value = {
            "email": "owner@example.com",
            "phone": "555-0100",
            "decision_makers": [{"name": "Maria Gomez", "title": "Owner"}, {"name": "Sam Lee", "title": "Manager"}],
            "employee_count_hint": "our team of 12",
            "years_in_business_hint": "serving the area since 1998",
            "certifications_hint": "Licensed, Bonded & Insured",
            "services_offered": ["Water heater repair", "Drain cleaning"],
            "social_links": ["https://facebook.com/example"],
            "found_anything": True,
        }

        enrichment.run_enrichment_job("job1", 1)

        mock_db.update_lead_enrichment.assert_called_once()
        _, kwargs = mock_db.update_lead_enrichment.call_args
        assert kwargs["contact_status"] == "enriched"
        assert kwargs["owner_name"] == "Maria Gomez"  # first decision maker used as primary
        assert kwargs["email"] == "owner@example.com"
        assert kwargs["site_phone"] == "555-0100"
        assert len(kwargs["decision_makers"]) == 2
        assert kwargs["services_offered"] == ["Water heater repair", "Drain cleaning"]
        assert kwargs["social_links"] == ["https://facebook.com/example"]

        final_call = mock_jobs.update_job.call_args
        assert final_call.kwargs.get("status") == "done"
        assert final_call.kwargs["result"]["services_offered"] == ["Water heater repair", "Drain cleaning"]


def test_run_enrichment_job_honest_when_nothing_found():
    fake_lead = type("L", (), {"website": "https://example.com", "id": 1})()
    fake_pages = [PageResult(path="/", url="https://example.com", ok=True, text="Just a landing page, no contact info")]

    with patch("enrichment.db") as mock_db, \
         patch("enrichment.jobs") as mock_jobs, \
         patch("enrichment.scraper") as mock_scraper, \
         patch("enrichment.generate_structured") as mock_gemini:
        mock_db.get_lead.return_value = fake_lead
        mock_scraper.fetch_site_context_sync.return_value = fake_pages
        mock_gemini.return_value = {
            "email": "", "phone": "", "decision_makers": [], "employee_count_hint": "",
            "years_in_business_hint": "", "certifications_hint": "", "services_offered": [],
            "social_links": [], "found_anything": False,
        }

        enrichment.run_enrichment_job("job1", 1)

        _, kwargs = mock_db.update_lead_enrichment.call_args
        assert kwargs["contact_status"] == "enrichment_failed"
        assert kwargs["owner_name"] is None
