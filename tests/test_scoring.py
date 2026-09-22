import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from models import Lead, BuyBox
from scoring import score_lead, bayesian_rating


def make_lead(**overrides):
    base = dict(
        id=1, company="Test Co", category="HVAC", address="123 Main St",
        city="Riverside", state="CA", phone="951-555-0100", website=None,
        google_rating=4.5, google_rating_count=100, price_level=None,
        place_id="abc123", source="Google Places",
    )
    base.update(overrides)
    return Lead(**base)


def test_strong_match_scores_tier_a():
    lead = make_lead(category="HVAC", google_rating=5.0, google_rating_count=300, phone="951-555-0100")
    score = score_lead(lead, BuyBox())
    assert score["tier"] == "A"
    assert score["fit_score"] >= 75


def test_off_category_scores_lower_than_on_category():
    on_category = make_lead(category="HVAC")
    off_category = make_lead(id=2, category="Auto Repair")
    bb = BuyBox()
    assert score_lead(on_category, bb)["fit_score"] > score_lead(off_category, bb)["fit_score"]


def test_missing_phone_caps_score_when_required():
    lead = make_lead(phone=None, google_rating=5.0, google_rating_count=500)
    bb = BuyBox(require_phone=True)
    score = score_lead(lead, bb)
    assert score["fit_score"] <= 20.0


def test_bayesian_rating_pulls_low_volume_toward_prior():
    # a 5.0 rating from just 1 review should NOT outrank a 4.8 from 900 reviews
    low_volume = bayesian_rating(5.0, 1)
    high_volume = bayesian_rating(4.8, 900)
    assert high_volume > low_volume


def test_missing_review_data_is_neutral_not_penalized():
    lead = make_lead(google_rating=None, google_rating_count=None)
    score = score_lead(lead, BuyBox())
    assert score["breakdown"]["reputation"] == 0.5


def test_evidence_coverage_reflects_real_data_presence():
    sparse = make_lead(phone=None, website=None, google_rating=None, google_rating_count=None)
    rich = make_lead(website="https://example.com", contact_status="enriched")
    assert score_lead(sparse, BuyBox())["evidence_coverage"] < score_lead(rich, BuyBox())["evidence_coverage"]
