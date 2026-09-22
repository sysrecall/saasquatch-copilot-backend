"""
A real, minimal job queue.

This is deliberately not Celery+Redis - at this project's scale that's
infrastructure the task doesn't need yet, and "real" doesn't mean "as heavy
as possible." What it needs is: don't block the HTTP request while a headless
browser loads a page and an LLM call runs (that's 5-15+ seconds, easily
timing out a request and definitely too long to hold a spinner on), and let
the frontend poll for status instead of guessing. A SQLite-backed jobs table
plus FastAPI's BackgroundTasks gives genuine async execution and a real
status a client can poll - if this needs to survive a process crash mid-job
or scale across multiple backend instances later, that's the point to
introduce Celery/RQ + Redis; this table's shape (id/status/result) carries
over directly.
"""
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Literal

DB_PATH = Path(__file__).parent / "data" / "copilot.db"

JobStatus = Literal["queued", "running", "done", "failed"]


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_jobs_table() -> None:
    conn = _conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            job_type TEXT NOT NULL,
            lead_id INTEGER,
            status TEXT NOT NULL DEFAULT 'queued',
            progress TEXT,
            result TEXT,
            error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def create_job(job_type: str, lead_id: Optional[int] = None) -> str:
    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    conn = _conn()
    conn.execute(
        "INSERT INTO jobs (id, job_type, lead_id, status, created_at, updated_at) VALUES (?,?,?,?,?,?)",
        (job_id, job_type, lead_id, "queued", now, now),
    )
    conn.commit()
    conn.close()
    return job_id


def update_job(
    job_id: str,
    status: Optional[JobStatus] = None,
    progress: Optional[str] = None,
    result: Optional[dict] = None,
    error: Optional[str] = None,
) -> None:
    conn = _conn()
    fields, values = [], []
    if status is not None:
        fields.append("status = ?")
        values.append(status)
    if progress is not None:
        fields.append("progress = ?")
        values.append(progress)
    if result is not None:
        fields.append("result = ?")
        values.append(json.dumps(result))
    if error is not None:
        fields.append("error = ?")
        values.append(error)
    fields.append("updated_at = ?")
    values.append(datetime.now(timezone.utc).isoformat())
    values.append(job_id)
    conn.execute(f"UPDATE jobs SET {', '.join(fields)} WHERE id = ?", values)
    conn.commit()
    conn.close()


def get_job(job_id: str) -> Optional[dict]:
    conn = _conn()
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    if d["result"]:
        d["result"] = json.loads(d["result"])
    return d
