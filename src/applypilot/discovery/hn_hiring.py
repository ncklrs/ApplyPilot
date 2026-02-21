"""Hacker News 'Who is Hiring?' scraper.

Finds the latest monthly "Ask HN: Who is hiring?" thread via the
HN Algolia API and parses top-level comments into job postings.

Each comment in the thread is a single company's job posting containing
company name, role, location, and description -- format varies but
follows loose conventions.
"""

import json
import logging
import re
import urllib.request
import urllib.error
from datetime import datetime, timezone

from applypilot.database import get_connection, init_db
from applypilot.discovery.utils import setup_proxy, strip_html

log = logging.getLogger(__name__)

UA = "Mozilla/5.0 (compatible; ApplyPilot/1.0)"
ALGOLIA_SEARCH = "https://hn.algolia.com/api/v1/search"
ALGOLIA_ITEMS = "https://hn.algolia.com/api/v1/items"

# HN comment text is HTML with <p> tags
_WHITESPACE = re.compile(r"\s+")
_HTML_TAG = re.compile(r"<[^>]+>")


def _fetch_json(url: str, timeout: int = 15) -> dict | None:
    """Fetch a URL and parse as JSON."""
    setup_proxy()
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, json.JSONDecodeError) as e:
        log.warning("Failed to fetch %s: %s", url, e)
        return None


def find_latest_thread() -> int | None:
    """Find the story ID of the latest 'Who is Hiring?' thread.

    Returns:
        HN story ID, or None if not found.
    """
    # Search for the most recent "Who is hiring?" post by whoishiring
    params = (
        "query=%22Ask+HN%3A+Who+is+hiring%22"
        "&tags=story,author_whoishiring"
        "&hitsPerPage=1"
    )
    data = _fetch_json(f"{ALGOLIA_SEARCH}?{params}")
    if not data or not data.get("hits"):
        # Fallback: search without author filter
        params = (
            "query=%22Ask+HN%3A+Who+is+hiring%22"
            "&tags=story"
            "&hitsPerPage=5"
        )
        data = _fetch_json(f"{ALGOLIA_SEARCH}?{params}")
        if not data or not data.get("hits"):
            log.warning("Could not find 'Who is Hiring?' thread")
            return None
        # Find the most recent one with the right title pattern
        for hit in data["hits"]:
            title = hit.get("title", "")
            if "who is hiring" in title.lower() and "freelancer" not in title.lower():
                return int(hit["objectID"])
        return None

    return int(data["hits"][0]["objectID"])


def _clean_comment(html_text: str) -> str:
    """Convert HN comment HTML to plain text."""
    text = html_text or ""
    text = text.replace("<p>", "\n\n").replace("</p>", "")
    text = text.replace("<br>", "\n").replace("<br/>", "\n")
    text = _HTML_TAG.sub("", text)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&#x27;", "'").replace("&quot;", '"')
    return text.strip()


def _extract_url(text: str) -> str | None:
    """Extract the first URL from text."""
    match = re.search(r"https?://[^\s<>\"')\]]+", text)
    return match.group(0).rstrip(".,;:") if match else None


def _parse_first_line(text: str) -> dict:
    """Parse the first line of a HN hiring comment for structured data.

    Typical format: "Company Name | Role | Location | Remote | URL"
    """
    lines = text.strip().split("\n")
    if not lines:
        return {}

    first_line = lines[0].strip()
    parts = [p.strip() for p in first_line.split("|")]

    result: dict = {}

    if parts:
        result["company"] = parts[0][:100]

    # Scan remaining parts for role, location, URL, remote hints
    for part in parts[1:]:
        lower = part.lower()
        if re.match(r"https?://", part):
            result["url"] = part
        elif any(kw in lower for kw in ("remote", "onsite", "hybrid", "office")):
            loc = result.get("location", "")
            result["location"] = f"{loc}, {part}".strip(", ") if loc else part
        elif any(kw in lower for kw in (
            "engineer", "developer", "designer", "manager", "lead",
            "architect", "scientist", "analyst", "devops", "sre",
            "frontend", "backend", "fullstack", "full-stack", "full stack",
        )):
            result["title"] = part
        elif len(part) > 2:
            # Assume location if it's a place-like string
            if result.get("location"):
                result["location"] += f", {part}"
            else:
                result["location"] = part

    return result


def parse_comments(thread_id: int, max_comments: int = 500) -> list[dict]:
    """Fetch and parse top-level comments from a HN hiring thread.

    Args:
        thread_id: HN story ID for the hiring thread.
        max_comments: Maximum comments to process.

    Returns:
        List of job dicts ready for store_jobs().
    """
    data = _fetch_json(f"{ALGOLIA_ITEMS}/{thread_id}")
    if not data:
        return []

    children = data.get("children", [])
    log.info("HN thread %d has %d top-level comments", thread_id, len(children))

    jobs: list[dict] = []
    for i, child in enumerate(children[:max_comments]):
        text = child.get("text", "")
        if not text:
            continue

        clean = _clean_comment(text)
        if len(clean) < 30:
            continue  # Too short to be a job posting

        parsed = _parse_first_line(clean)
        company = parsed.get("company", "Unknown")
        title = parsed.get("title", "")
        location = parsed.get("location", "")

        # Build a URL: prefer extracted URL, else link to the comment
        comment_id = child.get("id", "")
        url = parsed.get("url") or _extract_url(clean)
        if not url:
            url = f"https://news.ycombinator.com/item?id={comment_id}"

        # Use the full comment as description
        description = clean[:500]

        job = {
            "url": url,
            "title": title or f"Position at {company}",
            "salary": "",
            "description": description,
            "location": location,
            "full_description": clean,
            "application_url": parsed.get("url") or _extract_url(clean),
        }
        jobs.append(job)

    return jobs


def run_hn_discovery() -> dict:
    """Main entry point: find the latest HN hiring thread and extract jobs.

    Returns:
        {"new": int, "existing": int, "thread_id": int | None}
    """
    init_db()
    conn = get_connection()

    thread_id = find_latest_thread()
    if thread_id is None:
        log.warning("No HN 'Who is Hiring?' thread found.")
        return {"new": 0, "existing": 0, "thread_id": None}

    log.info("Found HN hiring thread: %d", thread_id)
    jobs = parse_comments(thread_id)
    log.info("Parsed %d job postings from HN thread", len(jobs))

    if not jobs:
        return {"new": 0, "existing": 0, "thread_id": thread_id}

    # Store with pre-enriched data (full_description already available)
    now = datetime.now(timezone.utc).isoformat()
    new = 0
    existing = 0

    for job in jobs:
        url = job.get("url")
        if not url:
            continue
        try:
            conn.execute(
                "INSERT INTO jobs "
                "(url, title, salary, description, location, site, strategy, "
                "discovered_at, full_description, application_url, detail_scraped_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    url,
                    job.get("title"),
                    job.get("salary"),
                    job.get("description"),
                    job.get("location"),
                    "Hacker News Who is Hiring?",
                    "hn_algolia",
                    now,
                    job.get("full_description"),
                    job.get("application_url"),
                    now,  # Mark as already enriched
                ),
            )
            new += 1
        except Exception:
            existing += 1

    conn.commit()
    log.info("HN hiring: %d new, %d existing", new, existing)
    return {"new": new, "existing": existing, "thread_id": thread_id}
