"""
Contact enrichment via real page rendering + LLM structured extraction.

Extracts everything genuinely findable on a real business website, not just
an email - phone, every named decision maker (not just one), team size,
years in business, licenses/certifications, services offered, and social
links. All of it grounded and non-guessing: the schema allows empty
fields/arrays, and "found nothing for this field" is treated as a valid,
honest result rather than something to retry or estimate around.
"""
from typing import Optional

import db
import jobs
import scraper
from gemini import generate_structured, GeminiError

ENRICHMENT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "email": {
            "type": "STRING",
            "description": "A real email address found verbatim in the text. Empty string if none is present - never invent one.",
        },
        "phone": {
            "type": "STRING",
            "description": "A phone number found verbatim on the site (from visible text or a tel: link). Empty string if none.",
        },
        "decision_makers": {
            "type": "ARRAY",
            "description": "Every person explicitly named in the text as owner, founder, manager, or similar. Empty array if none are named.",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "name": {"type": "STRING"},
                    "title": {"type": "STRING", "description": "Their stated title/role. Empty string if not given."},
                },
                "required": ["name", "title"],
            },
        },
        "employee_count_hint": {
            "type": "STRING",
            "description": "Only if the text explicitly states a team/staff size (e.g. 'our team of 12'), quote that phrase verbatim. Empty string otherwise - do not estimate.",
        },
        "years_in_business_hint": {
            "type": "STRING",
            "description": "Only if the text explicitly states how long they've operated or when founded (e.g. 'serving the area since 1998'), quote it verbatim. Empty string otherwise.",
        },
        "certifications_hint": {
            "type": "STRING",
            "description": "Verbatim mention of licenses, certifications, bonding, insurance, or accreditation (e.g. 'Licensed, Bonded & Insured', 'BBB A+ Rated'). Empty string if not mentioned.",
        },
        "services_offered": {
            "type": "ARRAY",
            "description": "Specific services or specialties explicitly listed on the site. Empty array if nothing is clearly listed.",
            "items": {"type": "STRING"},
        },
        "social_links": {
            "type": "ARRAY",
            "description": "Facebook/Instagram/LinkedIn/X/Yelp URLs found as links in the page. Empty array if none.",
            "items": {"type": "STRING"},
        },
        "found_anything": {
            "type": "BOOLEAN",
            "description": "true only if at least one field above is non-empty",
        },
    },
    "required": [
        "email", "phone", "decision_makers", "employee_count_hint",
        "years_in_business_hint", "certifications_hint", "services_offered",
        "social_links", "found_anything",
    ],
}


def _clean(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    value = value.strip()
    if not value or value.lower() in ("null", "n/a", "none", "unknown"):
        return None
    return value


def _clean_list(values) -> list:
    if not values:
        return []
    return [v.strip() for v in values if isinstance(v, str) and v.strip()]


def _clean_decision_makers(values) -> list:
    if not values:
        return []
    cleaned = []
    for v in values:
        name = _clean(v.get("name") if isinstance(v, dict) else None)
        if name:
            cleaned.append({"name": name, "title": _clean(v.get("title")) or ""})
    return cleaned


def _build_context(pages) -> str:
    parts = []
    for p in pages:
        if not p.ok:
            continue
        chunk = f"--- Page: {p.url} ---\n{p.text}"
        if p.mailto_links:
            chunk += f"\n(mailto links found in HTML: {', '.join(p.mailto_links)})"
        if p.tel_links:
            chunk += f"\n(tel links found in HTML: {', '.join(p.tel_links)})"
        parts.append(chunk)
    return "\n\n".join(parts)[:8000]


def extract_contact_info(pages, on_retry=None) -> dict:
    context = _build_context(pages)
    if not context.strip():
        raise GeminiError("no renderable page content was retrieved to extract from")
    prompt = (
        "Below is real text rendered from a real small business's website "
        "(homepage and/or contact/about pages), including any mailto: or "
        "tel: links found in the HTML. Extract ONLY information explicitly "
        "present in this text - do not guess, infer, or generate a "
        "plausible-sounding value for anything not literally stated. Use an "
        "empty string or empty array for any field that isn't mentioned. "
        "List every named decision-maker you find, not just the first "
        "one.\n\n" + context
    )
    # This runs as a background job, not a request someone is staring at a
    # spinner for - a generous retry budget is the whole point of moving
    # enrichment off the request/response path in the first place.
    return generate_structured(prompt, ENRICHMENT_SCHEMA, max_retries=6, on_retry=on_retry)


def run_enrichment_job(job_id: str, lead_id: int) -> None:
    """Entry point for the background task. Every exit path updates the job
    to a terminal state (done/failed) with a real reason - nothing here ever
    marks a job 'done' with fabricated results."""
    jobs.update_job(job_id, status="running", progress="looking up lead")
    lead = db.get_lead(lead_id)
    if not lead:
        jobs.update_job(job_id, status="failed", error="Lead not found")
        return
    if not lead.website:
        jobs.update_job(job_id, status="failed", error="No website on file for this lead")
        return

    jobs.update_job(job_id, progress=f"rendering {lead.website} (headless browser)")
    try:
        pages = scraper.fetch_site_context_sync(lead.website)
    except Exception as e:
        jobs.update_job(job_id, status="failed", error=f"Browser rendering failed: {e}")
        return

    ok_pages = [p for p in pages if p.ok]
    if not ok_pages:
        errs = "; ".join(p.error for p in pages if p.error) or "no pages loaded"
        jobs.update_job(job_id, status="failed", error=f"Could not load any page on this site: {errs}")
        return

    jobs.update_job(job_id, progress="asking Gemini to extract structured contact info")
    try:
        extracted = extract_contact_info(
            pages, on_retry=lambda msg: jobs.update_job(job_id, progress=f"Gemini: {msg}")
        )
    except GeminiError as e:
        jobs.update_job(job_id, status="failed", error=str(e))
        return

    email = _clean(extracted.get("email"))
    phone = _clean(extracted.get("phone"))
    decision_makers = _clean_decision_makers(extracted.get("decision_makers"))
    employee_hint = _clean(extracted.get("employee_count_hint"))
    years_hint = _clean(extracted.get("years_in_business_hint"))
    certifications = _clean(extracted.get("certifications_hint"))
    services = _clean_list(extracted.get("services_offered"))
    social = _clean_list(extracted.get("social_links"))

    primary_name = decision_makers[0]["name"] if decision_makers else None
    found_any = bool(email or phone or decision_makers or employee_hint or years_hint or certifications or services or social)
    contact_status = "enriched" if found_any else "enrichment_failed"

    db.update_lead_enrichment(
        lead_id,
        contact_status=contact_status,
        owner_name=primary_name,
        email=email,
        decision_makers=decision_makers,
        site_phone=phone,
        employee_count_hint=employee_hint,
        years_in_business_hint=years_hint,
        certifications_hint=certifications,
        services_offered=services,
        social_links=social,
    )

    jobs.update_job(job_id, status="done", result={
        "contact_status": contact_status,
        "email": email,
        "phone": phone,
        "decision_makers": decision_makers,
        "employee_count_hint": employee_hint,
        "years_in_business_hint": years_hint,
        "certifications_hint": certifications,
        "services_offered": services,
        "social_links": social,
        "pages_checked": [p.url for p in pages if p.ok],
    })