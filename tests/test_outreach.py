import sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).parent.parent))

from models import Lead
from outreach import generate_outreach, OutreachError


def make_lead(**overrides):
    base = dict(
        id=1, company="Test Co", category="HVAC", address="123 Main St",
        city="Riverside", state="CA", phone="951-555-0100", website=None,
        google_rating=4.5, google_rating_count=100, price_level=None,
        place_id="abc123", source="Google Places",
    )
    base.update(overrides)
    return Lead(**base)


def test_raises_real_error_instead_of_fake_draft_when_gemini_unconfigured():
    lead = make_lead()
    score = {"breakdown": {"category_fit": 1.0, "reputation": 0.8, "scale_signal": 0.6, "contactability": 0.4}}

    with patch("outreach.generate_structured", side_effect=OutreachError("GEMINI_API_KEY is not set")):
        try:
            generate_outreach(lead, score, cache_get=lambda k: None, cache_set=lambda k, v: None)
            assert False, "should have raised instead of returning a fallback draft"
        except OutreachError as e:
            assert "GEMINI_API_KEY" in str(e)


def test_returns_cached_draft_without_calling_gemini_again():
    lead = make_lead()
    score = {"breakdown": {"category_fit": 1.0, "reputation": 0.8, "scale_signal": 0.6, "contactability": 0.4}}
    cache = {}

    def cache_get(k):
        return cache.get(k)

    def cache_set(k, v):
        cache[k] = v

    with patch("outreach.generate_structured", return_value={"subject": "Hi", "body": "Real draft"}) as mock_gen:
        first = generate_outreach(lead, score, cache_get, cache_set)
        assert first["cached"] is False
        second = generate_outreach(lead, score, cache_get, cache_set)
        assert second["cached"] is True
        mock_gen.assert_called_once()  # second call should NOT hit Gemini again
