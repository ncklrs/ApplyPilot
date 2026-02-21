"""Ashby ATS direct API scraper: searches employer job boards.

Scrapes Ashby-powered career sites (Notion, Ramp, Linear, etc.)
via the public Posting API. Zero LLM, zero browser -- pure HTTP.

Employer registry is loaded from config/ashby.yaml.
"""

import json
import logging
import sqlite3
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import yaml

from applypilot import config
from applypilot.config import CONFIG_DIR
from applypilot.database import get_connection, init_db
from applypilot.discovery.utils import (
    load_location_filter,
    location_ok,
    matches_query,
    setup_proxy,
    strip_html,
    urlopen,
)

log = logging.getLogger(__name__)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

ASHBY_API_BASE = "https://api.ashbyhq.com"


# -- Employer registry from YAML --------------------------------------------

def load_employers() -> dict:
    """Load Ashby employer registry from config/ashby.yaml."""
    path = CONFIG_DIR / "ashby.yaml"
    if not path.exists():
        log.warning("ashby.yaml not found at %s", path)
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data.get("employers", {})


# -- Ashby API ---------------------------------------------------------------

def ashby_list_jobs(board_id: str) -> list[dict]:
    """List all jobs from an Ashby job board.

    Uses the public posting-api endpoint.

    Args:
        board_id: The employer's Ashby board ID / slug.

    Returns:
        List of job posting dicts.
    """
    url = f"{ASHBY_API_BASE}/posting-api/job-board/{board_id}"

    req = urllib.request.Request(url)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", UA)

    with urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
        return data.get("jobs", [])


def ashby_job_detail(posting_id: str) -> dict:
    """Fetch full job detail from Ashby posting API.

    Args:
        posting_id: The job posting ID.

    Returns:
        Job detail dict with description HTML.
    """
    url = f"{ASHBY_API_BASE}/posting-api/job-posting/{posting_id}"

    payload = json.dumps({"jobPostingId": posting_id}).encode()
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", UA)

    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


# -- Search + filter ---------------------------------------------------------

def search_employer(
    employer_key: str,
    employer: dict,
    search_text: str,
    accept_locs: list[str] | None = None,
    reject_locs: list[str] | None = None,
) -> list[dict]:
    """Search an Ashby employer's job board, filter by query and location.

    Args:
        employer_key: Registry key for the employer.
        employer: Employer config dict with 'name' and 'board_id'.
        search_text: Search query to filter job titles against.
        accept_locs: Location accept patterns.
        reject_locs: Location reject patterns.

    Returns:
        List of matched job dicts ready for storage.
    """
    board_id = employer["board_id"]
    log.info("%s: fetching jobs from Ashby board '%s'...", employer["name"], board_id)

    try:
        raw_jobs = ashby_list_jobs(board_id)
    except Exception as e:
        log.error("%s: API error: %s", employer["name"], e)
        return []

    log.info("%s: %d total jobs on board", employer["name"], len(raw_jobs))

    if accept_locs is None:
        accept_locs = []
    if reject_locs is None:
        reject_locs = []

    matched: list[dict] = []
    for job in raw_jobs:
        title = job.get("title", "")

        # Filter by search query
        if not matches_query(title, search_text):
            continue

        # Extract location
        location_str = job.get("location", "")
        if isinstance(location_str, dict):
            location_str = location_str.get("name", "")

        # Also check for remote in employment type
        employment_type = job.get("employmentType", "")
        is_remote = job.get("isRemote", False)
        if is_remote and location_str and "remote" not in location_str.lower():
            location_str = f"{location_str} (Remote)"

        # Filter by location
        if not location_ok(location_str, accept_locs, reject_locs):
            continue

        # Description: try descriptionHtml or descriptionPlain
        description = ""
        desc_html = job.get("descriptionHtml", "")
        if desc_html:
            description = strip_html(desc_html)
        elif job.get("descriptionPlain"):
            description = job["descriptionPlain"]

        # If description is short, try fetching detail
        if len(description) < 100:
            posting_id = job.get("id", "")
            if posting_id:
                try:
                    detail = ashby_job_detail(posting_id)
                    detail_html = detail.get("descriptionHtml", "")
                    if detail_html:
                        description = strip_html(detail_html)
                except Exception as e:
                    log.debug("Failed to fetch Ashby job detail for %s: %s", posting_id, e)

        # Build apply URL
        apply_url = job.get("applyUrl", "")
        if not apply_url and job.get("jobUrl"):
            apply_url = job["jobUrl"]
        if not apply_url:
            apply_url = f"https://jobs.ashbyhq.com/{board_id}/{job.get('id', '')}"

        matched.append({
            "title": title,
            "location": location_str,
            "description": description.strip(),
            "apply_url": apply_url,
            "job_id": job.get("id"),
            "employer_key": employer_key,
            "employer_name": employer["name"],
            "team": job.get("team", ""),
            "employment_type": employment_type,
        })

    log.info("%s: %d jobs matched query '%s'", employer["name"], len(matched), search_text)
    return matched


# -- DB storage --------------------------------------------------------------

def store_results(conn: sqlite3.Connection, jobs: list[dict]) -> tuple[int, int]:
    """Store Ashby jobs in DB. Returns (new, existing).

    Jobs arrive pre-enriched (full description + apply URL from the API),
    so detail_scraped_at is set at discovery time.
    """
    now = datetime.now(timezone.utc).isoformat()
    new = 0
    existing = 0

    for job in jobs:
        url = job.get("apply_url", "")
        if not url:
            continue

        description = job.get("description", "")
        short_desc = description[:500] if description else None
        full_description = description if len(description) > 200 else None
        detail_scraped_at = now if full_description else None

        site = job.get("employer_name", "Ashby")
        strategy = "ashby_api"

        try:
            conn.execute(
                "INSERT INTO jobs (url, title, salary, description, location, site, strategy, "
                "discovered_at, full_description, application_url, detail_scraped_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (url, job.get("title"), None, short_desc, job.get("location"),
                 site, strategy, now, full_description, url, detail_scraped_at),
            )
            new += 1
        except sqlite3.IntegrityError:
            existing += 1

    conn.commit()
    return new, existing


# -- Process one employer ----------------------------------------------------

def _process_one(
    employer_key: str,
    employers: dict,
    search_text: str,
    accept_locs: list[str],
    reject_locs: list[str],
) -> dict:
    """Search one employer, store results."""
    emp = employers[employer_key]

    try:
        jobs = search_employer(
            employer_key, emp, search_text,
            accept_locs=accept_locs,
            reject_locs=reject_locs,
        )
    except Exception as e:
        log.error("%s: ERROR searching '%s': %s", emp["name"], search_text, e)
        return {"employer": emp["name"], "query": search_text,
                "found": 0, "new": 0, "existing": 0, "error": str(e)}

    if not jobs:
        return {"employer": emp["name"], "query": search_text,
                "found": 0, "new": 0, "existing": 0}

    conn = get_connection()
    new, existing = store_results(conn, jobs)
    log.info("%s: %d new, %d already in DB", emp["name"], new, existing)

    return {"employer": emp["name"], "query": search_text,
            "found": len(jobs), "new": new, "existing": existing}


# -- Main orchestrator -------------------------------------------------------

def scrape_employers(
    search_text: str,
    employers: dict,
    employer_keys: list[str] | None = None,
    accept_locs: list[str] | None = None,
    reject_locs: list[str] | None = None,
    workers: int = 1,
) -> dict:
    """Run full scrape: list -> filter -> store."""
    if employer_keys is None:
        employer_keys = list(employers.keys())

    if accept_locs is None:
        accept_locs = []
    if reject_locs is None:
        reject_locs = []

    init_db()

    total_new = 0
    total_existing = 0
    total_found = 0
    errors = 0
    t0 = time.time()

    valid_keys = [k for k in employer_keys if k in employers]

    if workers > 1 and len(valid_keys) > 1:
        with ThreadPoolExecutor(max_workers=min(workers, len(valid_keys))) as pool:
            futures = {
                pool.submit(
                    _process_one, key, employers, search_text,
                    accept_locs, reject_locs,
                ): key
                for key in valid_keys
            }
            for future in as_completed(futures):
                result = future.result()
                total_new += result["new"]
                total_existing += result["existing"]
                total_found += result["found"]
                if "error" in result:
                    errors += 1
    else:
        for key in valid_keys:
            result = _process_one(key, employers, search_text, accept_locs, reject_locs)
            total_new += result["new"]
            total_existing += result["existing"]
            total_found += result["found"]
            if "error" in result:
                errors += 1

    elapsed = time.time() - t0
    log.info("[Ashby][%s] Done: %d found, %d new, %d dupes in %.0fs",
             search_text, total_found, total_new, total_existing, elapsed)

    return {"found": total_found, "new": total_new, "existing": total_existing}


# -- Public entry point ------------------------------------------------------

def run_ashby_discovery(employers: dict | None = None, workers: int = 1) -> dict:
    """Main entry point for Ashby-based job discovery.

    Args:
        employers: Override the employer registry. If None, loads from YAML.
        workers: Number of parallel threads. Default 1 (sequential).

    Returns:
        Dict with stats: found, new, existing, queries.
    """
    if employers is None:
        employers = load_employers()

    if not employers:
        log.warning("No Ashby employers configured. Create config/ashby.yaml.")
        return {"found": 0, "new": 0, "existing": 0, "queries": 0}

    search_cfg = config.load_search_config()
    queries_cfg = search_cfg.get("queries", [])
    accept_locs, reject_locs = load_location_filter(search_cfg)

    max_tier = search_cfg.get("ashby_max_tier", 2)
    queries = [q["query"] for q in queries_cfg if q.get("tier", 99) <= max_tier]

    if not queries:
        queries = [q["query"] for q in queries_cfg]

    if not queries:
        log.warning("No search queries configured in searches.yaml.")
        return {"found": 0, "new": 0, "existing": 0, "queries": 0}

    proxy = search_cfg.get("proxy")
    if proxy:
        setup_proxy(proxy)

    log.info("Ashby crawl: %d queries x %d employers (workers=%d)",
             len(queries), len(employers), workers)

    grand_new = 0
    grand_existing = 0
    grand_found = 0

    for i, query in enumerate(queries, 1):
        log.info("Query %d/%d: \"%s\"", i, len(queries), query)
        result = scrape_employers(
            search_text=query,
            employers=employers,
            accept_locs=accept_locs,
            reject_locs=reject_locs,
            workers=workers,
        )
        grand_new += result["new"]
        grand_existing += result["existing"]
        grand_found += result["found"]

    log.info("Ashby crawl done: %d found, %d new, %d existing across %d queries x %d employers",
             grand_found, grand_new, grand_existing, len(queries), len(employers))

    return {
        "found": grand_found,
        "new": grand_new,
        "existing": grand_existing,
        "queries": len(queries),
    }
