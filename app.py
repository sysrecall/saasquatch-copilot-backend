"""
SaaSquatch Copilot - backend

Endpoints:
  GET   /leads                    -> all leads with fit score, tier, data-quality flags
  GET   /leads/{id}               -> single lead detail
  PATCH /leads/{id}/website       -> attach a real website URL to a lead (enables enrichment)
  POST  /leads/{id}/enrich        -> start a real enrichment job (headless-browser scrape + LLM extraction)
  GET   /jobs/{id}                -> poll job status (queued/running/done/failed) + result
  POST  /leads/{id}/outreach      -> generate (or return cached) AI outreach draft for a lead
  GET   /export                   -> CSV export of the scored/flagged leads
  GET   /buybox                   -> current buy-box criteria
  POST  /buybox                   -> update the ICP buy-box criteria and re-score

Run: uvicorn app:app --reload --port 8000
"""
# IMPORTANT: this must run before any other local import. outreach.py and
# enrichment.py read env vars (GEMINI_API_KEY, etc.) at module import time -
# if .env isn't loaded first, they silently see "not configured" even when
# the person has genuinely put a key in their .env file. This was a real bug
# in an earlier version: adding a key to .env did nothing because nothing
# ever loaded that file into the process environment, so every outreach
# request silently fell through to the template fallback and looked "fake."
from dotenv import load_dotenv
load_dotenv()

import csv
import io
from typing import List

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import db
import jobs
from models import BuyBox
from scoring import score_lead
from data_quality import validity_flags
from outreach import generate_outreach, OutreachError
from enrichment import run_enrichment_job

app = FastAPI(title="SaaSquatch Copilot API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten to the deployed frontend origin in production
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup():
    db.init_db()


def _enriched_leads():
    leads = db.get_all_leads()
    buy_box = db.get_buy_box()
    out = []
    for lead in leads:
        score = score_lead(lead, buy_box)
        out.append({
            "lead": lead.model_dump(),
            "score": score,
            "data_quality_flags": validity_flags(lead),
        })
    out.sort(key=lambda r: -r["score"]["fit_score"])
    return out


@app.get("/leads")
def get_leads():
    return _enriched_leads()


@app.get("/leads/{lead_id}")
def get_lead(lead_id: int):
    for row in _enriched_leads():
        if row["lead"]["id"] == lead_id:
            return row
    raise HTTPException(404, "Lead not found")


class WebsitePatch(BaseModel):
    website: str


@app.patch("/leads/{lead_id}/website")
def patch_website(lead_id: int, body: WebsitePatch):
    if not db.get_lead(lead_id):
        raise HTTPException(404, "Lead not found")
    db.update_lead_website(lead_id, body.website)
    return db.get_lead(lead_id)


@app.post("/leads/{lead_id}/enrich", status_code=202)
def enrich_lead(lead_id: int, background_tasks: BackgroundTasks):
    lead = db.get_lead(lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")
    if not lead.website:
        raise HTTPException(400, "This lead has no website on file - add one first via PATCH /leads/{id}/website")
    job_id = jobs.create_job(job_type="enrich", lead_id=lead_id)
    background_tasks.add_task(run_enrichment_job, job_id, lead_id)
    return {"job_id": job_id, "status": "queued"}


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    job = jobs.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


@app.post("/leads/{lead_id}/outreach")
def create_outreach(lead_id: int):
    lead = db.get_lead(lead_id)
    if not lead:
        raise HTTPException(404, "Lead not found")
    buy_box = db.get_buy_box()
    score = score_lead(lead, buy_box)
    try:
        return generate_outreach(lead, score, db.get_cached_draft, db.set_cached_draft)
    except OutreachError as e:
        # Real error, surfaced honestly - never a fake-looking draft in its place.
        raise HTTPException(503, str(e))


@app.get("/buybox")
def get_buybox():
    return db.get_buy_box().model_dump()


@app.post("/buybox")
def update_buybox(buy_box: BuyBox):
    db.set_buy_box(buy_box)
    return _enriched_leads()


@app.get("/export")
def export_csv():
    rows = _enriched_leads()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "company", "category", "city", "state", "phone", "website",
        "google_rating", "google_rating_count", "contact_status", "email",
        "fit_score", "tier", "evidence_coverage", "data_quality_flags",
    ])
    for r in rows:
        l, s = r["lead"], r["score"]
        writer.writerow([
            l["company"], l["category"], l["city"], l["state"], l["phone"], l["website"],
            l["google_rating"], l["google_rating_count"], l["contact_status"], l["email"],
            s["fit_score"], s["tier"], s["evidence_coverage"],
            ";".join(r["data_quality_flags"]),
        ])
    buf.seek(0)
    return StreamingResponse(
        buf, media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=scored_leads.csv"},
    )


@app.get("/health")
def health():
    return {"status": "ok", "leads_loaded": len(db.get_all_leads())}
