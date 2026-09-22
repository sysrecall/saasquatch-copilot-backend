"""
AI-drafted outreach opener, via Gemini structured output.

No fallback template. An earlier version of this silently returned a
bracketed placeholder email ("[Your Name]") whenever Gemini wasn't
configured or the call failed, which looked like real output when it
wasn't. Now: if Gemini can't produce a draft, the caller gets a real error
and the UI shows that honestly instead of a fake-looking email.
"""
from typing import Dict, Any
from models import Lead
from gemini import generate_structured, GeminiError, GEMINI_MODEL

OutreachError = GeminiError  # re-exported so app.py has one error type to catch

OUTREACH_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "subject": {"type": "STRING", "description": "Email subject line, under 60 characters"},
        "body": {"type": "STRING", "description": "3-4 sentence email body, no signature block"},
    },
    "required": ["subject", "body"],
}


def _top_factor(breakdown: Dict[str, float]) -> str:
    label_map = {
        "category_fit": "how closely the business matches the target category",
        "reputation": "a strong, well-established review record",
        "scale_signal": "review volume suggesting an established customer base",
        "contactability": "having a working phone number and verified contact",
    }
    top_key = max(breakdown, key=breakdown.get)
    return label_map.get(top_key, "overall fit")


def generate_outreach(lead: Lead, score_result: Dict[str, Any], cache_get, cache_set) -> Dict[str, Any]:
    """cache_get/cache_set are injected (SQLite-backed in app.py) so a real
    draft survives a server restart. Raises GeminiError - callers must not
    substitute a fallback value for a real failure."""
    top_factor = _top_factor(score_result["breakdown"])
    key = f"{lead.id}:{top_factor}"

    cached = cache_get(key)
    if cached:
        return {**cached, "cached": True}

    prompt = (
        "Write a short, specific cold email opener from a business "
        "searcher/acquirer to a small business owner. No generic sales "
        "language. Ground it only in these real facts - do not invent "
        "any fact not listed below.\n\n"
        f"Company: {lead.company}\n"
        f"Category: {lead.category}\n"
        f"Location: {lead.city}, {lead.state}\n"
        f"Google rating: {lead.google_rating} ({lead.google_rating_count} reviews)\n"
        f"Why this lead scored well: {top_factor}\n"
    )
    parsed = generate_structured(prompt, OUTREACH_SCHEMA)  # raises GeminiError on failure
    result = {"subject": parsed["subject"], "body": parsed["body"], "source": f"llm:{GEMINI_MODEL}"}

    cache_set(key, result)
    return {**result, "cached": False}
