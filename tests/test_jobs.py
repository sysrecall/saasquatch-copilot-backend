import sys
import os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import jobs


def setup_function():
    # use a throwaway DB file per test run so tests don't collide with dev data
    jobs.DB_PATH = Path(__file__).parent / "test_jobs.db"
    if jobs.DB_PATH.exists():
        jobs.DB_PATH.unlink()
    jobs.init_jobs_table()


def teardown_function():
    if jobs.DB_PATH.exists():
        jobs.DB_PATH.unlink()


def test_create_job_starts_queued():
    job_id = jobs.create_job("enrich", lead_id=5)
    job = jobs.get_job(job_id)
    assert job["status"] == "queued"
    assert job["lead_id"] == 5
    assert job["job_type"] == "enrich"


def test_update_job_progress_and_status():
    job_id = jobs.create_job("enrich", lead_id=1)
    jobs.update_job(job_id, status="running", progress="rendering page")
    job = jobs.get_job(job_id)
    assert job["status"] == "running"
    assert job["progress"] == "rendering page"


def test_update_job_result_round_trips_as_dict():
    job_id = jobs.create_job("enrich", lead_id=1)
    jobs.update_job(job_id, status="done", result={"email": "a@b.com", "found": True})
    job = jobs.get_job(job_id)
    assert job["result"] == {"email": "a@b.com", "found": True}


def test_update_job_failure_records_error():
    job_id = jobs.create_job("enrich", lead_id=1)
    jobs.update_job(job_id, status="failed", error="no website on file")
    job = jobs.get_job(job_id)
    assert job["status"] == "failed"
    assert job["error"] == "no website on file"


def test_get_job_returns_none_for_unknown_id():
    assert jobs.get_job("does-not-exist") is None
