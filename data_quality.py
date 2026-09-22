"""
Per-lead data-quality flags.

Note on scope: this used to live in dedupe.py alongside cross-lead duplicate
detection. That duplicate-detection logic was removed - it solved a
multi-source-overlap problem (the same business appearing twice after
merging results from several providers), which is an ingestion-layer
concern. This project never built a real multi-source ingestion pipeline
(leads come from one static seed file), so there was no code path that could
ever produce a duplicate for it to catch. Keeping tested code around for a
problem that structurally cannot occur yet is the same kind of "looks like
it works" issue this project has been actively hunting down elsewhere -
so it's gone, not just disabled.

validity_flags() is a different, still-legitimate concern: it's about
whether a single lead's own data is complete enough to trust, which matters
regardless of how many sources fed into it.
"""
import re
from typing import List
from models import Lead

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")


def validity_flags(lead: Lead) -> List[str]:
    flags = []
    if not lead.phone:
        flags.append("missing_phone")
    if not lead.website:
        flags.append("no_website_on_file")
    if lead.contact_status != "enriched":
        flags.append("contact_unenriched")
    if lead.google_rating is None or lead.google_rating_count is None:
        flags.append("no_review_data")
    elif lead.google_rating_count < 10:
        flags.append("low_review_volume")
    return flags
