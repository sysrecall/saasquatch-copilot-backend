# SaaSquatch Copilot — Backend

FastAPI backend for a lead-qualification and outreach pipeline built on top of SaaSquatch's data model, for Caprae Capital's Full Stack Developer AI-Readiness Pre-Screening Challenge. Pairs with a separate frontend: **[FRONTEND_REPO_LINK]**.

## What it does

- **Fit & reputation scoring** (`scoring.py`) — scores real businesses against a configurable buy box (category, location, Bayesian-adjusted Google rating, review volume as an honest scale proxy — never a fabricated revenue/headcount number).
- **LLM-based contact enrichment** (`enrichment.py`, `scraper.py`) — a real headless browser (Playwright) renders a lead's website, handling client-side-rendered sites a plain HTTP request would miss, and Gemini extracts structured contact info (email, phone, every named decision-maker, certifications, services, years in business) grounded only in what's actually on the page. Runs as a real background job (`jobs.py`, SQLite-backed) with pollable live status, not a blocking request.
- **AI-drafted outreach** (`outreach.py`) — Gemini structured output for a personalized email opener, grounded in a lead's real data. No fallback template: a failure is a real error, never fake-looking content.
- **Shared Gemini client** (`gemini.py`) — structured output via `responseSchema`, with a proactive rate-limit throttle and retry-with-backoff on timeouts/429/5xx, since the free-tier API genuinely times out and rate-limits in practice.
- **Real SQLite persistence** (`db.py`) — leads, buy box, outreach cache, and jobs all survive a restart. Includes a self-healing schema migration: an existing database missing newer columns gets them added automatically on startup rather than crashing.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium      # required for enrichment
cp .env.example .env             # add your GEMINI_API_KEY - see below
uvicorn app:app --reload --port 8000
```

API docs (auto-generated): `http://localhost:8000/docs`
First run seeds the database from `data/seed_leads.json` automatically.

## Environment variables

All in `.env.example` — copy it to `.env` and fill in:

| Variable | Default | Notes |
|---|---|---|
| `GEMINI_API_KEY` | *(none)* | Get a free key at [aistudio.google.com/apikey](https://aistudio.google.com/apikey). Without it, outreach/enrichment fail with a clear error instead of faking output. |
| `GEMINI_MODEL` | `gemini-3.5-flash` | |
| `GEMINI_RPM_LIMIT` | `8` | Requests-per-minute the client throttles itself to. Google doesn't publish a fixed free-tier number anymore (their docs say capacity "is not guaranteed and may vary") — check your actual limit at [aistudio.google.com/rate-limit](https://aistudio.google.com/rate-limit) and adjust. |
| `GEMINI_TIMEOUT_S` | `45` | Per-request timeout. Raise this before raising retries if you're seeing timeouts. |
| `GEMINI_MAX_RETRIES` | `3` | Governs the synchronous outreach endpoint. Enrichment (a background job) always uses 6, since nothing is blocked waiting on it. |

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/leads` | All leads with fit score, tier, and data-quality flags |
| GET | `/leads/{id}` | Single lead detail |
| PATCH | `/leads/{id}/website` | Attach a website URL (enables enrichment) |
| POST | `/leads/{id}/enrich` | Start a real enrichment job (202, returns `job_id`) |
| GET | `/jobs/{id}` | Poll job status (queued/running/done/failed) |
| POST | `/leads/{id}/outreach` | Generate (or return cached) AI outreach draft |
| GET | `/buybox` | Current buy-box criteria |
| POST | `/buybox` | Update buy-box criteria and re-score |
| GET | `/export` | CSV export of scored leads |
| GET | `/health` | Liveness check |

## Tests

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

Covers scoring, data-quality flags, the job queue, the Gemini client's retry/throttle behavior, enrichment orchestration (scraper and LLM calls mocked), and a schema-migration regression test.

## Data

`data/seed_leads.json` — 13 real small businesses (HVAC, plumbing, dental, landscaping, veterinary) across five California cities, pulled live from Google Places: real names, addresses, phone numbers, ratings, and review counts. No employee count or revenue field — there's no free, legal source for that data for arbitrary small businesses, so it isn't estimated or faked.

## Deployment

Deployed on Render. If deploying this yourself as its own repo:
- **Root Directory**: repo root (this README's directory)
- **Build Command**: `pip install -r requirements.txt && playwright install chromium --with-deps`
- **Start Command**: `uvicorn app:app --host 0.0.0.0 --port $PORT`
- Set the environment variables above in Render's dashboard, not just `.env` (`.env` isn't committed to git).

## What's out of scope

- Real employee count / revenue data (no free legal source exists)
- LinkedIn as a data source (violates its Terms of Service; also actively blocks automation)
- Deduplication — deliberately removed. It's an ingestion-layer concern (merging overlapping results from multiple scraped sources), and this project doesn't build that ingestion layer; a single-source seed list has no duplicates to find.
- Auth / multi-tenancy
