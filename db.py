"""
SQLite persistence layer.

Earlier versions of this project claimed a database in the README but
actually kept everything in an in-memory Python list that reset on every
server restart. This module is the fix: leads, the buy box, and outreach
drafts are all genuinely persisted to a SQLite file and survive a restart.
"""
import json
import sqlite3
from pathlib import Path
from typing import List, Optional
from models import Lead, BuyBox
import jobs as jobs_module

DB_PATH = Path(__file__).parent / "data" / "copilot.db"
SEED_PATH = Path(__file__).parent / "data" / "seed_leads.json"

# Source of truth for the leads table shape. CREATE TABLE IF NOT EXISTS only
# creates the table on a brand-new database - it does nothing to an existing
# one, so a database created before a field like `decision_makers` was added
# would keep crashing on every read with a KeyError, forever, until someone
# manually deleted the file. This happened for real: adding six enrichment
# fields in one change and only documenting "delete your old db" wasn't
# enough - the fix has to be in code, not in a README someone has to
# remember to read. _migrate_leads_table adds whatever's missing on startup.
LEADS_COLUMNS = {
    "company": "TEXT NOT NULL",
    "category": "TEXT NOT NULL",
    "address": "TEXT NOT NULL",
    "city": "TEXT NOT NULL",
    "state": "TEXT NOT NULL",
    "phone": "TEXT",
    "website": "TEXT",
    "google_rating": "REAL",
    "google_rating_count": "INTEGER",
    "price_level": "INTEGER",
    "place_id": "TEXT NOT NULL DEFAULT ''",
    "source": "TEXT NOT NULL DEFAULT 'Google Places'",
    "contact_status": "TEXT NOT NULL DEFAULT 'unenriched'",
    "owner_name": "TEXT",
    "email": "TEXT",
    "decision_makers": "TEXT NOT NULL DEFAULT '[]'",
    "site_phone": "TEXT",
    "employee_count_hint": "TEXT",
    "years_in_business_hint": "TEXT",
    "certifications_hint": "TEXT",
    "services_offered": "TEXT NOT NULL DEFAULT '[]'",
    "social_links": "TEXT NOT NULL DEFAULT '[]'",
    "existing_tools": "TEXT NOT NULL DEFAULT '[]'",
}


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _migrate_leads_table(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(leads)").fetchall()}
    added = []
    for col, decl in LEADS_COLUMNS.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE leads ADD COLUMN {col} {decl}")
            added.append(col)
    if added:
        conn.commit()
        print(f"[db] migrated leads table - added missing column(s): {', '.join(added)}")


def init_db() -> None:
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY,
            company TEXT NOT NULL,
            category TEXT NOT NULL,
            address TEXT NOT NULL,
            city TEXT NOT NULL,
            state TEXT NOT NULL,
            phone TEXT,
            website TEXT,
            google_rating REAL,
            google_rating_count INTEGER,
            price_level INTEGER,
            place_id TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'Google Places',
            contact_status TEXT NOT NULL DEFAULT 'unenriched',
            owner_name TEXT,
            email TEXT,
            decision_makers TEXT NOT NULL DEFAULT '[]',
            site_phone TEXT,
            employee_count_hint TEXT,
            years_in_business_hint TEXT,
            certifications_hint TEXT,
            services_offered TEXT NOT NULL DEFAULT '[]',
            social_links TEXT NOT NULL DEFAULT '[]',
            existing_tools TEXT NOT NULL DEFAULT '[]'
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS buy_box (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            data TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS outreach_cache (
            cache_key TEXT PRIMARY KEY,
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            source TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    _migrate_leads_table(conn)
    jobs_module.init_jobs_table()

    row_count = conn.execute("SELECT COUNT(*) AS n FROM leads").fetchone()["n"]
    if row_count == 0 and SEED_PATH.exists():
        seed_rows = json.loads(SEED_PATH.read_text())
        for row in seed_rows:
            _insert_lead(conn, Lead(**row))
        conn.commit()

    if conn.execute("SELECT COUNT(*) AS n FROM buy_box").fetchone()["n"] == 0:
        conn.execute("INSERT INTO buy_box (id, data) VALUES (1, ?)", (BuyBox().model_dump_json(),))
        conn.commit()

    conn.close()


def _insert_lead(conn: sqlite3.Connection, lead: Lead) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO leads
           (id, company, category, address, city, state, phone, website,
            google_rating, google_rating_count, price_level, place_id,
            source, contact_status, owner_name, email, decision_makers,
            site_phone, employee_count_hint, years_in_business_hint,
            certifications_hint, services_offered, social_links, existing_tools)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            lead.id, lead.company, lead.category, lead.address, lead.city, lead.state,
            lead.phone, lead.website, lead.google_rating, lead.google_rating_count,
            lead.price_level, lead.place_id, lead.source, lead.contact_status,
            lead.owner_name, lead.email, json.dumps(lead.decision_makers),
            lead.site_phone, lead.employee_count_hint, lead.years_in_business_hint,
            lead.certifications_hint, json.dumps(lead.services_offered),
            json.dumps(lead.social_links), json.dumps(lead.existing_tools),
        ),
    )


def _row_to_lead(row: sqlite3.Row) -> Lead:
    d = dict(row)
    d["existing_tools"] = json.loads(d["existing_tools"])
    d["decision_makers"] = json.loads(d["decision_makers"])
    d["services_offered"] = json.loads(d["services_offered"])
    d["social_links"] = json.loads(d["social_links"])
    return Lead(**d)


def get_all_leads() -> List[Lead]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM leads ORDER BY id").fetchall()
    conn.close()
    return [_row_to_lead(r) for r in rows]


def get_lead(lead_id: int) -> Optional[Lead]:
    conn = get_connection()
    row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    conn.close()
    return _row_to_lead(row) if row else None


def update_lead_website(lead_id: int, website: str) -> None:
    conn = get_connection()
    conn.execute("UPDATE leads SET website = ? WHERE id = ?", (website, lead_id))
    conn.commit()
    conn.close()


def update_lead_enrichment(
    lead_id: int,
    contact_status: str,
    owner_name: Optional[str],
    email: Optional[str],
    decision_makers: Optional[list] = None,
    site_phone: Optional[str] = None,
    employee_count_hint: Optional[str] = None,
    years_in_business_hint: Optional[str] = None,
    certifications_hint: Optional[str] = None,
    services_offered: Optional[list] = None,
    social_links: Optional[list] = None,
    backfill_phone_if_missing: bool = True,
) -> None:
    """One call that stores everything a real enrichment pass can find. If
    the lead had no phone on file (Google Places doesn't always have one)
    and the site enrichment found one, that phone is used to fill the gap -
    but a phone we already trust from Places is never silently overwritten."""
    conn = get_connection()
    if backfill_phone_if_missing and site_phone:
        existing = conn.execute("SELECT phone FROM leads WHERE id = ?", (lead_id,)).fetchone()
        if existing and not existing["phone"]:
            conn.execute("UPDATE leads SET phone = ? WHERE id = ?", (site_phone, lead_id))

    conn.execute(
        """UPDATE leads SET
            contact_status = ?, owner_name = ?, email = ?, decision_makers = ?,
            site_phone = ?, employee_count_hint = ?, years_in_business_hint = ?,
            certifications_hint = ?, services_offered = ?, social_links = ?
           WHERE id = ?""",
        (
            contact_status, owner_name, email, json.dumps(decision_makers or []),
            site_phone, employee_count_hint, years_in_business_hint,
            certifications_hint, json.dumps(services_offered or []),
            json.dumps(social_links or []), lead_id,
        ),
    )
    conn.commit()
    conn.close()


def get_buy_box() -> BuyBox:
    conn = get_connection()
    row = conn.execute("SELECT data FROM buy_box WHERE id = 1").fetchone()
    conn.close()
    return BuyBox(**json.loads(row["data"]))


def set_buy_box(buy_box: BuyBox) -> None:
    conn = get_connection()
    conn.execute("UPDATE buy_box SET data = ? WHERE id = 1", (buy_box.model_dump_json(),))
    conn.commit()
    conn.close()


def get_cached_draft(cache_key: str) -> Optional[dict]:
    conn = get_connection()
    row = conn.execute(
        "SELECT subject, body, source FROM outreach_cache WHERE cache_key = ?", (cache_key,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def set_cached_draft(cache_key: str, draft: dict) -> None:
    conn = get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO outreach_cache (cache_key, subject, body, source) VALUES (?,?,?,?)",
        (cache_key, draft["subject"], draft["body"], draft["source"]),
    )
    conn.commit()
    conn.close()
