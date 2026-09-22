"""
Scoring engine: turns a real lead into a transparent, explainable fit score
+ tier, plus a separate evidence-coverage (data-quality) score.

Every factor here is computed from data that is actually present on the
lead - nothing here estimates headcount or revenue. If that kind of
firmographic data matters for your use case, wire in a paid provider (Apollo,
Crunchbase) in enrichment.py and add it as a factor here; this version is
honest about not having it rather than pretending to.
"""
import math
from typing import Dict, Any
from models import Lead, BuyBox

WEIGHTS = {
    "category_fit": 0.30,
    "reputation": 0.30,
    "scale_signal": 0.15,
    "contactability": 0.25,
}

# Bayesian average prior: pulls a rating with very few reviews back toward a
# neutral prior instead of letting e.g. a single 5-star review outscore a
# genuinely well-reviewed business. Standard technique for rating reliability
# (same idea IMDb uses for its "weighted rating").
RATING_PRIOR = 4.0
RATING_PRIOR_WEIGHT = 15


def _range_fit(value: float, low: float, high: float, softness: float = 0.35) -> float:
    """For 'should land inside a target window' metrics: 1.0 inside
    [low, high], linear falloff outside. Not used for rating/review-volume
    below - those are 'more is better' metrics and need _linear_scale so
    they don't all flatten to the same score once past the floor."""
    if low <= value <= high:
        return 1.0
    span = max(high - low, 1)
    margin = span * softness
    dist = (low - value) if value < low else (value - high)
    return max(0.0, 1.0 - dist / margin)


def _linear_scale(value: float, low: float, high: float) -> float:
    """For 'more is better' metrics: 0 at/below low, 1 at/above high, linear
    in between - so e.g. a 4.9-rated business still outscores a 4.1-rated
    one instead of both capping out at 1.0 past a floor."""
    if value <= low:
        return 0.0
    if value >= high:
        return 1.0
    return (value - low) / (high - low)


def _category_fit(lead_category: str, targets: list[str]) -> float:
    lc = lead_category.lower()
    if any(t.lower() in lc for t in targets):
        return 1.0
    lc_words = set(lc.replace("/", " ").split())
    for t in targets:
        if lc_words & set(t.lower().replace("/", " ").split()):
            return 0.5
    return 0.0


def bayesian_rating(rating: float, rating_count: int) -> float:
    return (rating_count * rating + RATING_PRIOR_WEIGHT * RATING_PRIOR) / (rating_count + RATING_PRIOR_WEIGHT)


def _reputation_score(lead: Lead, buy_box: BuyBox) -> float:
    if lead.google_rating is None or lead.google_rating_count is None:
        return 0.5  # no data - neutral, not penalized
    adj = bayesian_rating(lead.google_rating, lead.google_rating_count)
    return _linear_scale(adj, buy_box.min_rating, 5.0)


def _scale_signal(lead: Lead, buy_box: BuyBox) -> float:
    """Review volume as a rough, openly-labeled proxy for how established a
    business is online - NOT a revenue or headcount estimate, and shown to
    the user only as 'review volume'. Log-scaled since review counts span
    a wide range (dozens to thousands) and raw counts would let one
    outlier dominate."""
    if lead.google_rating_count is None:
        return 0.5
    floor = math.log1p(buy_box.min_review_count)
    ceiling = math.log1p(buy_box.min_review_count * 10)
    return _linear_scale(math.log1p(lead.google_rating_count), floor, ceiling)


def _contactability(lead: Lead, buy_box: BuyBox) -> float:
    score = 0.0
    if lead.phone:
        score += 0.4
    if lead.contact_status == "enriched" and (lead.owner_name or lead.email):
        score += 0.6
    elif buy_box.require_enriched_contact:
        score = min(score, 0.2)
    return min(score, 1.0)


def _evidence_coverage(lead: Lead) -> float:
    fields = [
        lead.phone, lead.website, lead.google_rating,
        lead.google_rating_count, lead.contact_status == "enriched",
    ]
    populated = sum(1 for f in fields if f not in (None, "", 0, False))
    return round(populated / len(fields), 2)


def score_lead(lead: Lead, buy_box: BuyBox) -> Dict[str, Any]:
    category_fit = _category_fit(lead.category, buy_box.target_categories)
    reputation = _reputation_score(lead, buy_box)
    scale_signal = _scale_signal(lead, buy_box)
    contactability = _contactability(lead, buy_box)

    fit_score = round(min((
        category_fit * WEIGHTS["category_fit"]
        + reputation * WEIGHTS["reputation"]
        + scale_signal * WEIGHTS["scale_signal"]
        + contactability * WEIGHTS["contactability"]
    ) * 100, 100.0), 1)

    if buy_box.require_phone and not lead.phone:
        fit_score = min(fit_score, 20.0)

    if fit_score >= 75:
        tier = "A"
    elif fit_score >= 55:
        tier = "B"
    elif fit_score >= 35:
        tier = "C"
    else:
        tier = "D"

    return {
        "lead_id": lead.id,
        "fit_score": fit_score,
        "tier": tier,
        "evidence_coverage": _evidence_coverage(lead),
        "breakdown": {
            "category_fit": round(category_fit, 2),
            "reputation": round(reputation, 2),
            "scale_signal": round(scale_signal, 2),
            "contactability": round(contactability, 2),
        },
    }
