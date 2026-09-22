"""
Data models + the configurable "buy box" (ICP definition).

NOTE ON WHAT'S REAL: this schema deliberately does NOT include employee_count
or estimated_revenue. Those fields existed in an earlier mock version of this
project and were fabricated - there is no free, legal data source that
provides real firmographic data (headcount, revenue) for arbitrary small
businesses. Real providers for that (Apollo, Crunchbase, Clearbit) require
paid API keys this project doesn't have. Rather than invent plausible-looking
numbers, this version scores on signals that ARE genuinely available for
free: category, location, Google rating + review volume, and phone/contact
presence. See enrichment.py for how owner/email contact data is obtained
(or honestly marked unavailable) instead of assumed.
"""
from pydantic import BaseModel
from typing import Optional, List


class Lead(BaseModel):
    id: int
    company: str
    category: str
    address: str
    city: str
    state: str
    phone: Optional[str] = None
    website: Optional[str] = None
    google_rating: Optional[float] = None
    google_rating_count: Optional[int] = None
    price_level: Optional[int] = None
    place_id: str
    source: str = "Google Places"

    # Contact enrichment - starts empty on every real lead. Populated only by
    # a real LLM extraction job in enrichment.py, grounded in actual scraped
    # page text, never fabricated at seed time.
    contact_status: str = "unenriched"  # "unenriched" | "enriched" | "enrichment_failed"
    owner_name: Optional[str] = None
    email: Optional[str] = None
    decision_makers: List[dict] = []       # [{"name": ..., "title": ...}, ...] - every name found, not just one
    site_phone: Optional[str] = None       # phone found on the site itself, kept separate from the Places-sourced `phone`
    employee_count_hint: Optional[str] = None      # verbatim quote from the site, if one exists - never estimated
    years_in_business_hint: Optional[str] = None   # verbatim quote, e.g. "serving since 1998"
    certifications_hint: Optional[str] = None      # verbatim quote, e.g. "Licensed, Bonded & Insured"
    services_offered: List[str] = []       # specific services explicitly listed on the site
    social_links: List[str] = []           # Facebook/Instagram/LinkedIn/etc. URLs found on the site
    existing_tools: List[str] = []


class BuyBox(BaseModel):
    """The searcher's acquisition criteria - editable per user in the UI."""
    target_categories: List[str] = ["HVAC", "Plumbing", "Dental", "Landscaping", "Veterinary"]
    target_cities: List[str] = []  # empty = no geography filter
    min_rating: float = 4.0
    min_review_count: int = 50
    require_phone: bool = True
    require_enriched_contact: bool = False
