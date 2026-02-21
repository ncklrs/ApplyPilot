"""Greenhouse ATS direct API scraper: searches employer job boards.

Scrapes Greenhouse-powered career sites (Stripe, Cloudflare, Discord, etc.)
via the public Boards API. Zero LLM, zero browser -- pure HTTP.

Employer registry is loaded from config/greenhouse.yaml.
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


# -- Employer registry from YAML --------------------------------------------

def load_employers() -> dict:
    """Load Greenhouse employer registry from config/greenhouse.yaml."""
    path = CONFIG_DIR / "greenhouse.yaml"
    if not path.exists():
        log.warning("greenhouse.yaml not found at %s", path)
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data.get("employers", {})


# -- Greenhouse API ----------------------------------------------------------

def greenhouse_list_jobs(board_token: str, content: bool = True) -> list[dict]:
    """List all jobs from a Greenhouse job board.

    Args:
        board_token: The employer's Greenhouse board token (e.g. "stripe").
        content: If True, include full job descriptions in the response.

    Returns:
        List of job dicts from the API.
    """
    url = f"https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs"
    if content:
        url += "?content=true"

    req = urllib.request.Request(url)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", UA)

    with urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
        return data.get("jobs", [])


def greenhouse_job_detail(board_token: str, job_id: int) -> dict:
    """Fetch full job detail from Greenhouse API.

    Args:
        board_token: The employer's Greenhouse board token.
        job_id: The job posting ID.

    Returns:
        Job detail dict.
    """
    url = f"https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs/{job_id}"

    req = urllib.request.Request(url)
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
    """Search a Greenhouse employer's job board, filter by query and location.

    Args:
        employer_key: Registry key for the employer.
        employer: Employer config dict with 'name' and 'board_token'.
        search_text: Search query to filter job titles against.
        accept_locs: Location accept patterns.
        reject_locs: Location reject patterns.

    Returns:
        List of matched job dicts ready for storage.
    """
    board_token = employer["board_token"]
    log.info("%s: fetching jobs from Greenhouse board '%s'...", employer["name"], board_token)

    try:
        raw_jobs = greenhouse_list_jobs(board_token, content=True)
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
        loc = job.get("location", {})
        location_str = loc.get("name", "") if isinstance(loc, dict) else str(loc)

        # Filter by location
        if not location_ok(location_str, accept_locs, reject_locs):
            continue

        # Extract description (HTML -> plain text)
        content_html = job.get("content", "")
        description = strip_html(content_html) if content_html else ""

        matched.append({
            "title": title,
            "location": location_str,
            "description": description,
            "apply_url": job.get("absolute_url", ""),
            "job_id": job.get("id"),
            "updated_at": job.get("updated_at", ""),
            "employer_key": employer_key,
            "employer_name": employer["name"],
            "departments": [d.get("name", "") for d in job.get("departments", [])],
        })

    log.info("%s: %d jobs matched query '%s'", employer["name"], len(matched), search_text)
    return matched


# -- DB storage --------------------------------------------------------------

def store_results(conn: sqlite3.Connection, jobs: list[dict]) -> tuple[int, int]:
    """Store Greenhouse jobs in DB. Returns (new, existing).

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

        site = job.get("employer_name", "Greenhouse")
        strategy = "greenhouse_api"

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
    """Run full scrape: list -> filter -> store.

    Sequential by default. When workers > 1, processes employers in parallel.
    """
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
    log.info("[Greenhouse][%s] Done: %d found, %d new, %d dupes in %.0fs",
             search_text, total_found, total_new, total_existing, elapsed)

    return {"found": total_found, "new": total_new, "existing": total_existing}


# -- Public entry point ------------------------------------------------------

def run_greenhouse_discovery(employers: dict | None = None, workers: int = 1) -> dict:
    """Main entry point for Greenhouse-based job discovery.

    Loads employer registry from config/greenhouse.yaml (or uses the provided
    dict), then loads search queries from the user's search config.

    Args:
        employers: Override the employer registry. If None, loads from YAML.
        workers: Number of parallel threads. Default 1 (sequential).

    Returns:
        Dict with stats: found, new, existing, queries.
    """
    if employers is None:
        employers = load_employers()

    if not employers:
        log.warning("No Greenhouse employers configured. Create config/greenhouse.yaml.")
        return {"found": 0, "new": 0, "existing": 0, "queries": 0}

    search_cfg = config.load_search_config()
    queries_cfg = search_cfg.get("queries", [])
    accept_locs, reject_locs = load_location_filter(search_cfg)

    max_tier = search_cfg.get("greenhouse_max_tier", 2)
    queries = [q["query"] for q in queries_cfg if q.get("tier", 99) <= max_tier]

    if not queries:
        queries = [q["query"] for q in queries_cfg]

    if not queries:
        log.warning("No search queries configured in searches.yaml.")
        return {"found": 0, "new": 0, "existing": 0, "queries": 0}

    proxy = search_cfg.get("proxy")
    if proxy:
        setup_proxy(proxy)

    log.info("Greenhouse crawl: %d queries x %d employers (workers=%d)",
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

    log.info("Greenhouse crawl done: %d found, %d new, %d existing across %d queries x %d employers",
             grand_found, grand_new, grand_existing, len(queries), len(employers))

    return {
        "found": grand_found,
        "new": grand_new,
        "existing": grand_existing,
        "queries": len(queries),
    }
