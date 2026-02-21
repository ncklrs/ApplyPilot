"""Email inbox monitor: scan for application responses and update job status.

Connects to Gmail (or any IMAP server) to find emails related to
job applications -- confirmations, interview invites, rejections, and
follow-ups. Cross-references with jobs in the database by matching
sender domains to job URLs / company names.

Requires: IMAP_EMAIL and IMAP_PASSWORD in .env
Supports Gmail App Passwords (recommended over OAuth for CLI tools).
"""

import email
import email.utils
import imaplib
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from email.header import decode_header
from urllib.parse import urlparse

from applypilot.config import load_env
from applypilot.database import get_connection, init_db

log = logging.getLogger(__name__)

# Email classification patterns
_CONFIRMATION_PATTERNS = [
    re.compile(r"application.*received", re.I),
    re.compile(r"thank.*for.*applying", re.I),
    re.compile(r"we.*received.*application", re.I),
    re.compile(r"application.*submitted", re.I),
    re.compile(r"application.*confirmation", re.I),
    re.compile(r"your.*application.*to", re.I),
]

_INTERVIEW_PATTERNS = [
    re.compile(r"interview.*schedul", re.I),
    re.compile(r"invit.*to.*interview", re.I),
    re.compile(r"phone.*screen", re.I),
    re.compile(r"technical.*interview", re.I),
    re.compile(r"next.*steps.*interview", re.I),
    re.compile(r"meet.*the.*team", re.I),
    re.compile(r"we.*like.*to.*speak", re.I),
    re.compile(r"move.*forward.*candidacy", re.I),
    re.compile(r"we.*impressed", re.I),
]

_REJECTION_PATTERNS = [
    re.compile(r"not.*moving.*forward", re.I),
    re.compile(r"decided.*not.*to.*proceed", re.I),
    re.compile(r"position.*has.*been.*filled", re.I),
    re.compile(r"unfortunately.*not.*selected", re.I),
    re.compile(r"will.*not.*be.*moving", re.I),
    re.compile(r"other.*candidates.*more.*closely", re.I),
    re.compile(r"not.*right.*fit", re.I),
    re.compile(r"regret.*to.*inform", re.I),
    re.compile(r"not.*able.*to.*offer", re.I),
    re.compile(r"we.*appreciate.*interest.*however", re.I),
]

_FOLLOW_UP_PATTERNS = [
    re.compile(r"follow.*up", re.I),
    re.compile(r"checking.*in", re.I),
    re.compile(r"update.*on.*application", re.I),
    re.compile(r"status.*application", re.I),
]


def _decode_subject(msg) -> str:
    """Decode an email subject header."""
    raw = msg.get("Subject", "")
    decoded_parts = decode_header(raw)
    parts = []
    for data, charset in decoded_parts:
        if isinstance(data, bytes):
            parts.append(data.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(data)
    return " ".join(parts)


def _get_body(msg) -> str:
    """Extract plain text body from an email message."""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            if content_type == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
        # Fall back to HTML if no plain text
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    html = payload.decode(charset, errors="replace")
                    # Strip HTML tags for basic matching
                    return re.sub(r"<[^>]+>", " ", html)
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
    return ""


def classify_email(subject: str, body: str) -> str | None:
    """Classify an email as application-related.

    Args:
        subject: Email subject line.
        body: Email body text.

    Returns:
        One of: "confirmation", "interview", "rejection", "follow_up", or None.
    """
    combined = f"{subject}\n{body[:2000]}"

    # Check in priority order (interview > rejection > confirmation > follow_up)
    for pattern in _INTERVIEW_PATTERNS:
        if pattern.search(combined):
            return "interview"

    for pattern in _REJECTION_PATTERNS:
        if pattern.search(combined):
            return "rejection"

    for pattern in _CONFIRMATION_PATTERNS:
        if pattern.search(combined):
            return "confirmation"

    for pattern in _FOLLOW_UP_PATTERNS:
        if pattern.search(combined):
            return "follow_up"

    return None


def _extract_sender_domain(msg) -> str:
    """Extract the sender's domain from an email."""
    from_addr = msg.get("From", "")
    # Parse email address from "Name <email@domain.com>" format
    match = re.search(r"[\w.+-]+@([\w.-]+)", from_addr)
    if match:
        return match.group(1).lower()
    return ""


def match_to_jobs(sender_domain: str, company_name: str | None = None) -> list[dict]:
    """Find jobs in the database that match a sender domain.

    Matches by:
    1. Job URL containing the sender domain
    2. Application URL containing the sender domain
    3. Job site name matching the company name (fuzzy)

    Args:
        sender_domain: Domain from the email sender.
        company_name: Optional company name extracted from email.

    Returns:
        List of matching job dicts.
    """
    conn = get_connection()

    # Strip common email subdomains
    clean_domain = sender_domain
    for prefix in ("mail.", "email.", "noreply.", "notifications.", "careers."):
        if clean_domain.startswith(prefix):
            clean_domain = clean_domain[len(prefix):]

    # Get the base domain (e.g., "stripe.com" from "notifications.stripe.com")
    parts = clean_domain.split(".")
    if len(parts) > 2:
        clean_domain = ".".join(parts[-2:])

    domain_like = f"%{clean_domain}%"

    rows = conn.execute(
        "SELECT url, title, site, application_url, apply_status, applied_at "
        "FROM jobs WHERE url LIKE ? OR application_url LIKE ? "
        "ORDER BY applied_at DESC NULLS LAST LIMIT 10",
        (domain_like, domain_like),
    ).fetchall()

    if rows:
        return [dict(zip(rows[0].keys(), row)) for row in rows]

    # Try matching by site name
    if company_name:
        name_like = f"%{company_name}%"
        rows = conn.execute(
            "SELECT url, title, site, application_url, apply_status, applied_at "
            "FROM jobs WHERE site LIKE ? "
            "ORDER BY applied_at DESC NULLS LAST LIMIT 10",
            (name_like,),
        ).fetchall()
        if rows:
            return [dict(zip(rows[0].keys(), row)) for row in rows]

    return []


def scan_inbox(
    since_days: int = 7,
    imap_host: str | None = None,
    imap_email: str | None = None,
    imap_password: str | None = None,
) -> list[dict]:
    """Scan email inbox for application-related messages.

    Args:
        since_days: Look back this many days.
        imap_host: IMAP server hostname. Default: Gmail.
        imap_email: Email address. Default: from IMAP_EMAIL env var.
        imap_password: Password or app password. Default: from IMAP_PASSWORD env var.

    Returns:
        List of matched email dicts with keys:
        subject, sender, sender_domain, date, classification, matched_jobs.
    """
    load_env()

    host = imap_host or os.environ.get("IMAP_HOST", "imap.gmail.com")
    email_addr = imap_email or os.environ.get("IMAP_EMAIL", "")
    password = imap_password or os.environ.get("IMAP_PASSWORD", "")

    if not email_addr or not password:
        raise ValueError(
            "Email credentials not configured. "
            "Set IMAP_EMAIL and IMAP_PASSWORD in ~/.applypilot/.env"
        )

    # Calculate date range
    since_date = (datetime.now() - timedelta(days=since_days)).strftime("%d-%b-%Y")

    log.info("Connecting to %s as %s...", host, email_addr)

    try:
        imap = imaplib.IMAP4_SSL(host)
        imap.login(email_addr, password)
    except imaplib.IMAP4.error as e:
        raise ConnectionError(f"IMAP login failed: {e}") from e

    try:
        imap.select("INBOX", readonly=True)

        # Search for recent emails
        _, msg_ids = imap.search(None, f'(SINCE "{since_date}")')
        if not msg_ids[0]:
            log.info("No emails found since %s", since_date)
            return []

        ids = msg_ids[0].split()
        log.info("Scanning %d emails from the last %d days...", len(ids), since_days)

        matches = []

        for msg_id in ids:
            # Fetch headers first (lightweight) to classify by subject
            _, hdr_data = imap.fetch(msg_id, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
            if not hdr_data or not hdr_data[0]:
                continue

            hdr_raw = hdr_data[0][1]
            if not isinstance(hdr_raw, bytes):
                continue

            hdr_msg = email.message_from_bytes(hdr_raw)
            subject = _decode_subject(hdr_msg)
            sender_domain = _extract_sender_domain(hdr_msg)

            # Quick classify by subject only — skip full body fetch for non-matches
            classification = classify_email(subject, "")
            if not classification:
                continue

            # Promising email — fetch full body for deeper classification
            _, data = imap.fetch(msg_id, "(RFC822)")
            if not data or not data[0]:
                continue

            raw = data[0][1]
            if not isinstance(raw, bytes):
                continue

            msg = email.message_from_bytes(raw)
            body = _get_body(msg)
            sender_domain = _extract_sender_domain(msg)

            # Re-classify with full body for more accurate result
            classification = classify_email(subject, body)
            if not classification:
                continue

            # Try to match with jobs in DB
            matched_jobs = match_to_jobs(sender_domain)

            date_str = msg.get("Date", "") or hdr_msg.get("Date", "")
            parsed_date = email.utils.parsedate_to_datetime(date_str) if date_str else None

            matches.append({
                "subject": subject,
                "sender": msg.get("From", ""),
                "sender_domain": sender_domain,
                "date": parsed_date.isoformat() if parsed_date else "",
                "classification": classification,
                "matched_jobs": matched_jobs,
            })

        log.info("Found %d application-related emails", len(matches))
        return matches

    finally:
        try:
            imap.close()
            imap.logout()
        except Exception:
            pass


def update_from_inbox(matches: list[dict]) -> dict:
    """Update job statuses in the database based on inbox scan results.

    Args:
        matches: List of email match dicts from scan_inbox().

    Returns:
        {"updated": int, "unmatched": int}
    """
    conn = get_connection()
    now = datetime.now(timezone.utc).isoformat()
    updated = 0
    unmatched = 0

    for match in matches:
        classification = match["classification"]
        jobs = match["matched_jobs"]

        if not jobs:
            unmatched += 1
            continue

        for job in jobs:
            conn.execute(
                "UPDATE jobs SET inbox_status = ?, inbox_updated_at = ? WHERE url = ?",
                (classification, now, job["url"]),
            )
            updated += 1
            log.info(
                "Updated %s -> %s (from: %s)",
                job["title"][:30], classification, match["sender_domain"],
            )

    conn.commit()
    return {"updated": updated, "unmatched": unmatched}
