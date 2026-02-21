"""Fuzzy deduplication for job postings.

Supplements the URL-based primary key dedup with title+company fuzzy matching
to detect duplicate job postings from different sources.
"""

import logging
import re
import sqlite3

from applypilot.database import get_connection

log = logging.getLogger(__name__)


def _normalize(text: str) -> str:
    """Normalize text for fuzzy comparison.

    Lowercases, strips punctuation, collapses whitespace.
    """
    if not text:
        return ""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text


def _similarity(a: str, b: str) -> float:
    """Compute word-level Jaccard similarity between two strings.

    Returns a value between 0.0 (no overlap) and 1.0 (identical).
    """
    if not a or not b:
        return 0.0

    words_a = set(_normalize(a).split())
    words_b = set(_normalize(b).split())

    if not words_a or not words_b:
        return 0.0

    intersection = words_a & words_b
    union = words_a | words_b
    return len(intersection) / len(union)


def _normalize_company(name: str) -> str:
    """Normalize company name for comparison.

    Removes common suffixes (Inc, Ltd, Corp, etc.) and normalizes.
    """
    if not name:
        return ""
    name = _normalize(name)
    # Remove common corporate suffixes
    suffixes = [
        r"\binc\b", r"\bltd\b", r"\bcorp\b", r"\bcorporation\b",
        r"\bllc\b", r"\bllp\b", r"\bco\b", r"\bcompany\b",
        r"\bgroup\b", r"\bholdings\b", r"\binternational\b",
        r"\bglobal\b", r"\btechnologies\b", r"\bsolutions\b",
    ]
    for suffix in suffixes:
        name = re.sub(suffix, "", name)
    return re.sub(r"\s+", " ", name).strip()


def find_duplicates(
    conn: sqlite3.Connection | None = None,
    title_threshold: float = 0.8,
    company_threshold: float = 0.7,
) -> list[tuple[str, str, float]]:
    """Find likely duplicate job postings based on fuzzy title+company matching.

    Only compares jobs that have different URLs (URL-based dedup is already done
    at insert time). Groups jobs by normalized company name, then checks title
    similarity within each group.

    Args:
        conn: Database connection. Uses get_connection() if None.
        title_threshold: Minimum title similarity to consider a duplicate (0-1).
        company_threshold: Minimum company name similarity (0-1).

    Returns:
        List of (url_keep, url_remove, similarity_score) tuples.
        The job with the longer full_description is kept.
    """
    if conn is None:
        conn = get_connection()

    rows = conn.execute(
        "SELECT url, title, site, full_description FROM jobs "
        "WHERE title IS NOT NULL ORDER BY discovered_at"
    ).fetchall()

    if len(rows) < 2:
        return []

    # Group by normalized company name
    company_groups: dict[str, list[dict]] = {}
    for row in rows:
        job = {"url": row[0], "title": row[1], "site": row[2] or "",
               "desc_len": len(row[3] or "")}
        norm_company = _normalize_company(job["site"])
        if not norm_company:
            continue
        company_groups.setdefault(norm_company, []).append(job)

    # Also do cross-company comparison for similar company names
    company_keys = list(company_groups.keys())
    merged_groups: list[list[dict]] = []
    merged: set[str] = set()

    for i, key_a in enumerate(company_keys):
        if key_a in merged:
            continue
        group = list(company_groups[key_a])
        for key_b in company_keys[i + 1:]:
            if key_b in merged:
                continue
            if _similarity(key_a, key_b) >= company_threshold:
                group.extend(company_groups[key_b])
                merged.add(key_b)
        merged.add(key_a)
        if len(group) > 1:
            merged_groups.append(group)

    # Within each group, find title duplicates
    duplicates: list[tuple[str, str, float]] = []
    seen_pairs: set[tuple[str, str]] = set()

    for group in merged_groups:
        for i, job_a in enumerate(group):
            for job_b in group[i + 1:]:
                pair = tuple(sorted([job_a["url"], job_b["url"]]))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)

                sim = _similarity(job_a["title"], job_b["title"])
                if sim >= title_threshold:
                    # Keep the one with longer description
                    if job_a["desc_len"] >= job_b["desc_len"]:
                        duplicates.append((job_a["url"], job_b["url"], sim))
                    else:
                        duplicates.append((job_b["url"], job_a["url"], sim))

    return duplicates


def remove_duplicates(
    conn: sqlite3.Connection | None = None,
    title_threshold: float = 0.8,
    company_threshold: float = 0.7,
    dry_run: bool = False,
) -> dict:
    """Find and optionally remove duplicate job postings.

    Args:
        conn: Database connection.
        title_threshold: Minimum title similarity.
        company_threshold: Minimum company name similarity.
        dry_run: If True, report duplicates but don't delete.

    Returns:
        {"found": int, "removed": int, "duplicates": list}
    """
    if conn is None:
        conn = get_connection()

    dupes = find_duplicates(conn, title_threshold, company_threshold)

    if not dupes:
        log.info("No fuzzy duplicates found.")
        return {"found": 0, "removed": 0, "duplicates": []}

    log.info("Found %d fuzzy duplicate pairs.", len(dupes))

    removed = 0
    if not dry_run:
        for _keep_url, remove_url, _sim in dupes:
            # Only remove if the job hasn't been scored/tailored/applied
            row = conn.execute(
                "SELECT fit_score, tailored_resume_path, applied_at FROM jobs WHERE url = ?",
                (remove_url,),
            ).fetchone()
            if row and (row[0] is not None or row[1] is not None or row[2] is not None):
                continue  # Don't remove jobs that have been processed
            conn.execute("DELETE FROM jobs WHERE url = ?", (remove_url,))
            removed += 1
        conn.commit()

    log.info("Fuzzy dedup: %d found, %d removed.", len(dupes), removed)

    return {
        "found": len(dupes),
        "removed": removed,
        "duplicates": [(k, r, f"{s:.2f}") for k, r, s in dupes[:20]],
    }
