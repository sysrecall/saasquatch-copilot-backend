import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from models import Lead
from data_quality import validity_flags


def make_lead(**overrides):
    base = dict(
        id=1, company="Test Co", category="HVAC", address="123 Main St",
        city="Riverside", state="CA", phone="951-555-0100", website=None,
        google_rating=4.5, google_rating_count=100, price_level=None,
        place_id="abc123", source="Google Places",
    )
    base.update(overrides)
    return Lead(**base)


def test_validity_flags_missing_phone():
    lead = make_lead(phone=None)
    assert "missing_phone" in validity_flags(lead)


def test_validity_flags_low_review_volume():
    lead = make_lead(google_rating_count=3)
    assert "low_review_volume" in validity_flags(lead)


def test_validity_flags_unenriched_by_default():
    lead = make_lead()
    assert "contact_unenriched" in validity_flags(lead)


def test_validity_flags_clean_lead_has_fewer_flags():
    messy = make_lead(phone=None, website=None, google_rating=None, google_rating_count=None)
    clean = make_lead(website="https://example.com", contact_status="enriched")
    assert len(validity_flags(clean)) < len(validity_flags(messy))
