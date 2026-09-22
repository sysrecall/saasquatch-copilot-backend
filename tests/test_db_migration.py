import sys
import sqlite3
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import db


def setup_function():
    db.DB_PATH = Path(__file__).parent / "test_migration.db"
    if db.DB_PATH.exists():
        db.DB_PATH.unlink()


def teardown_function():
    if db.DB_PATH.exists():
        db.DB_PATH.unlink()


def test_old_schema_self_heals_without_data_loss():
    """Regression test for a real bug: a database created before the
    enrichment-field columns existed used to crash every read with
    KeyError('decision_makers') instead of migrating."""
    conn = sqlite3.connect(db.DB_PATH)
    conn.execute("""
        CREATE TABLE leads (
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
            existing_tools TEXT NOT NULL DEFAULT '[]'
        )
    """)
    conn.execute(
        "INSERT INTO leads (id, company, category, address, city, state, place_id) "
        "VALUES (1, 'Old Schema Co', 'HVAC', '1 Main St', 'Riverside', 'CA', 'abc123')"
    )
    conn.commit()
    conn.close()

    db.init_db()  # should migrate, not crash

    lead = db.get_lead(1)
    assert lead.company == "Old Schema Co"  # pre-existing data survived
    assert lead.decision_makers == []       # new columns default safely
    assert lead.site_phone is None
    assert lead.services_offered == []


def test_fresh_database_migration_is_a_noop():
    db.init_db()
    first_run_leads = db.get_all_leads()
    db.init_db()  # calling again should not error or duplicate/alter anything
    assert len(db.get_all_leads()) == len(first_run_leads)
