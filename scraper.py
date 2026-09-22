"""
Headless-browser page fetching via Playwright.

Why a real browser instead of requests.get(): a plain HTTP GET only returns
the initial server response. Many small-business sites (Squarespace, Wix,
React/Vue-built sites) render their actual contact info client-side after
JS runs - a raw GET would return an near-empty shell and enrichment would
always fail on those sites, not because the data isn't there but because we
never rendered the page that contains it.

Wait strategy for client-side rendering: navigate with wait_until="networkidle"
first (no in-flight requests for 500ms - catches most SPAs once their initial
data fetch completes), then add a fixed extra delay on top, since some sites
keep background polling (analytics, chat widgets) that never reaches a true
network-idle state, which would otherwise make Playwright wait needlessly or
give up too early.

HONESTY NOTE: this code is written against Playwright's current documented
API and is internally consistent, but the sandboxed environment this project
was built in cannot download Playwright's browser binary (network egress is
allowlisted to a small set of domains, and Playwright's CDN isn't one of
them - confirmed by testing before writing this file, not assumed). This
means the actual browser launch has NOT been run end-to-end here. Run
`playwright install chromium` and test against a real site before relying on
this in production.
"""
import asyncio
from dataclasses import dataclass, field
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

CANDIDATE_PATHS = ["", "/contact", "/contact-us", "/about", "/about-us"]
NAV_TIMEOUT_MS = 15_000
POST_LOAD_DELAY_MS = 2_500  # extra settle time for CSR sites past networkidle
MAX_CHARS_PER_PAGE = 4_000  # keep the eventual LLM prompt small and cheap
BROWSER_LAUNCH_TIMEOUT_S = 20  # a missing/broken browser install must fail fast, not hang
OVERALL_TIMEOUT_S = 90  # hard ceiling on the whole multi-page fetch


class ScraperError(Exception):
    """Raised when the browser can't be launched at all - as opposed to a
    single page failing to load, which is recorded per-page in PageResult
    instead of raising, since other candidate paths might still work."""


@dataclass
class PageResult:
    path: str
    url: str
    ok: bool
    text: str = ""
    mailto_links: list[str] = field(default_factory=list)
    tel_links: list[str] = field(default_factory=list)
    error: str = ""


async def _fetch_one(context, base_url: str, path: str) -> PageResult:
    url = f"{base_url.rstrip('/')}{path}"
    page = await context.new_page()
    try:
        try:
            await page.goto(url, wait_until="networkidle", timeout=NAV_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            # some sites never go fully idle (chat widgets, analytics beacons)
            # - if we at least reached DOM content, keep going rather than
            # treating this as a hard failure
            pass

        await page.wait_for_timeout(POST_LOAD_DELAY_MS)

        text = await page.inner_text("body")
        mailto = await page.eval_on_selector_all(
            "a[href^='mailto:']", "els => els.map(e => e.getAttribute('href'))"
        )
        tel = await page.eval_on_selector_all(
            "a[href^='tel:']", "els => els.map(e => e.getAttribute('href'))"
        )
        return PageResult(
            path=path or "/", url=url, ok=True,
            text=text[:MAX_CHARS_PER_PAGE],
            mailto_links=mailto, tel_links=tel,
        )
    except Exception as e:
        return PageResult(path=path or "/", url=url, ok=False, error=str(e))
    finally:
        await page.close()


async def fetch_site_context(base_url: str) -> list[PageResult]:
    """Renders the homepage + a few likely contact/about paths and returns
    what was found on each - including ones that failed, so the caller can
    tell 'we tried and found nothing' apart from 'every page errored.'"""
    results: list[PageResult] = []
    async with async_playwright() as p:
        try:
            browser = await asyncio.wait_for(
                p.chromium.launch(headless=True), timeout=BROWSER_LAUNCH_TIMEOUT_S
            )
        except asyncio.TimeoutError:
            raise ScraperError(
                f"Browser did not start within {BROWSER_LAUNCH_TIMEOUT_S}s - "
                "is it installed? Run `playwright install chromium`."
            )
        except Exception as e:
            raise ScraperError(f"Could not launch headless browser: {e}") from e

        try:
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (compatible; SaaSquatchCopilot/1.0; +enrichment-bot)"
            )
            for path in CANDIDATE_PATHS:
                result = await _fetch_one(context, base_url, path)
                results.append(result)
                if result.ok and (result.mailto_links or "@" in result.text):
                    # found something promising - no need to keep hitting
                    # more pages on this site
                    break
        finally:
            await browser.close()
    return results


def fetch_site_context_sync(base_url: str) -> list[PageResult]:
    """Sync wrapper for callers (like a BackgroundTasks job) that aren't
    already in an async context. Enforces a hard overall timeout so a stuck
    browser or network can never leave a job at 'running' forever."""
    async def _with_timeout():
        return await asyncio.wait_for(fetch_site_context(base_url), timeout=OVERALL_TIMEOUT_S)

    try:
        return asyncio.run(_with_timeout())
    except asyncio.TimeoutError:
        raise ScraperError(f"Scraping this site took longer than {OVERALL_TIMEOUT_S}s and was cancelled.")
