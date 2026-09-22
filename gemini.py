"""
Shared Gemini API client for structured output.

One place that calls Gemini, used by both outreach.py (draft an email) and
enrichment.py (extract contact fields from scraped page text). Both need the
same thing: send text, get back JSON matching a schema, guaranteed valid by
construction via responseSchema.

Deliberately raises on failure instead of returning a fallback value - see
the "no fake fallback" note further down for why.

RETRIES AND RATE LIMITING

Two separate mechanisms, because they solve different problems:

1. A proactive throttle (_throttle) that sleeps BEFORE sending a request if
   sending now would exceed a configured requests-per-minute budget. This is
   what makes usage "slow but reliable" instead of firing requests and
   hoping - it spaces calls out on purpose.

2. Reactive retry-with-backoff for anything that still fails: timeouts,
   network errors, HTTP 429 (rate limited), and HTTP 5xx (server error).
   4xx errors other than 429 are NOT retried - a bad API key or malformed
   request will fail identically every time, so retrying just wastes the
   rate-limit budget on a call that can't succeed.

On the specific rate-limit number: Google's own rate-limits documentation
(https://ai.google.dev/gemini-api/docs/rate-limits, checked while writing
this) does not publish a fixed per-model free-tier number anymore - it says
plainly that "specified rate limits are not guaranteed and actual capacity
may vary" and points to a per-account dashboard
(https://aistudio.google.com/rate-limit) instead. Third-party sources
disagree with each other (5, 10, and 15 RPM for gemini-2.5-flash all show up
depending on when they were written). Rather than hardcode a number I can't
verify is right for your account, GEMINI_RPM_LIMIT defaults to a conservative
8 and is meant to be tuned to what your account's dashboard actually shows.
The retry logic is what actually keeps things working even if this number
turns out to be wrong in either direction.
"""
import os
import json
import random
import threading
import time
import requests
from config import settings

GEMINI_API_KEY = settings.gemini_api_key
GEMINI_MODEL = settings.gemini_model
API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

GEMINI_RPM_LIMIT = settings.gemini_rpm_limit
REQUEST_TIMEOUT_S = settings.gemini_timeout_s
DEFAULT_MAX_RETRIES = settings.gemini_max_retries
BASE_BACKOFF_S = 2.0
MAX_BACKOFF_S = 30.0


class GeminiError(Exception):
    """Raised whenever a structured Gemini call can't be completed after all
    retries are exhausted - missing key, non-retryable HTTP error, or a
    response that doesn't match the requested schema. Callers must handle
    this explicitly rather than silently substituting fake content. An
    earlier version of this project had outreach.py silently fall back to a
    canned template whenever Gemini wasn't configured or failed, and it
    looked like a demo email had been genuinely generated when it hadn't -
    that fallback was removed; this is what replaced it."""


def is_configured() -> bool:
    return bool(GEMINI_API_KEY)


# --- proactive throttle -----------------------------------------------

_request_times: list[float] = []
_rate_lock = threading.Lock()


def _throttle() -> None:
    """Blocks until sending a request now would stay within GEMINI_RPM_LIMIT
    over the trailing 60 seconds. Module-level and lock-protected so it
    coordinates correctly across concurrent background jobs, not just within
    one request."""
    with _rate_lock:
        now = time.monotonic()
        window_start = now - 60
        while _request_times and _request_times[0] < window_start:
            _request_times.pop(0)
        if len(_request_times) >= GEMINI_RPM_LIMIT:
            sleep_for = 60 - (now - _request_times[0]) + 0.5
            if sleep_for > 0:
                time.sleep(sleep_for)
        _request_times.append(time.monotonic())


def _backoff_seconds(attempt: int) -> float:
    return min(MAX_BACKOFF_S, BASE_BACKOFF_S * (2 ** (attempt - 1))) + random.uniform(0, 1)


# --- main entry point ---------------------------------------------------

def generate_structured(
    prompt: str,
    schema: dict,
    timeout: int = None,
    max_retries: int = None,
    on_retry=None,
) -> dict:
    """Send `prompt`, get back JSON matching `schema` (Gemini's OpenAPI-subset
    Schema format: {"type": "OBJECT", "properties": {...}, "required": [...]}).
    Retries transient failures with backoff; raises GeminiError once retries
    are exhausted or on a non-retryable error. Never returns a placeholder.

    `on_retry`, if given, is called with a short status string before each
    backoff sleep - lets a caller (e.g. a background job) surface "waiting,
    retrying" instead of looking frozen during a long retry sequence."""
    if not GEMINI_API_KEY:
        raise GeminiError(
            "GEMINI_API_KEY is not set. Add it to backend/.env and restart the "
            "server (get a free key at https://aistudio.google.com/apikey)."
        )

    timeout = timeout or REQUEST_TIMEOUT_S
    max_retries = max_retries if max_retries is not None else DEFAULT_MAX_RETRIES

    last_error: GeminiError | None = None
    for attempt in range(1, max_retries + 1):
        _throttle()
        is_last_attempt = attempt == max_retries

        try:
            resp = requests.post(
                API_URL,
                headers={"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "responseMimeType": "application/json",
                        "responseSchema": schema,
                    },
                },
                timeout=timeout,
            )
        except requests.Timeout:
            last_error = GeminiError(f"Gemini request timed out after {timeout}s (attempt {attempt}/{max_retries})")
            if is_last_attempt:
                raise last_error
            wait = _backoff_seconds(attempt)
            if on_retry:
                on_retry(f"timed out on attempt {attempt}/{max_retries}, retrying in {wait:.0f}s")
            time.sleep(wait)
            continue
        except requests.RequestException as e:
            last_error = GeminiError(f"Network error calling Gemini: {e}")
            if is_last_attempt:
                raise last_error
            wait = _backoff_seconds(attempt)
            if on_retry:
                on_retry(f"network error on attempt {attempt}/{max_retries}, retrying in {wait:.0f}s")
            time.sleep(wait)
            continue

        if resp.status_code == 429:
            last_error = GeminiError(f"Gemini rate limit hit (429) after {max_retries} attempts: {resp.text[:300]}")
            if is_last_attempt:
                raise last_error
            retry_after = resp.headers.get("Retry-After")
            wait = float(retry_after) if retry_after else _backoff_seconds(attempt)
            if on_retry:
                on_retry(f"rate limited (attempt {attempt}/{max_retries}), waiting {wait:.0f}s")
            time.sleep(wait)
            continue

        if resp.status_code >= 500:
            last_error = GeminiError(f"Gemini returned HTTP {resp.status_code} after {max_retries} attempts: {resp.text[:300]}")
            if is_last_attempt:
                raise last_error
            wait = _backoff_seconds(attempt)
            if on_retry:
                on_retry(f"Gemini server error (attempt {attempt}/{max_retries}), retrying in {wait:.0f}s")
            time.sleep(wait)
            continue

        if resp.status_code != 200:
            # Non-retryable: a bad key, bad request, etc. will fail the same
            # way every time - retrying just burns the rate-limit budget.
            raise GeminiError(f"Gemini returned HTTP {resp.status_code}: {resp.text[:300]}")

        try:
            text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(text)  # guaranteed valid JSON by responseSchema
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            raise GeminiError(f"Unexpected Gemini response shape: {e}") from e

    raise last_error or GeminiError("Gemini call failed after retries")
