"""Shared utilities for job discovery scrapers.

Common functions used across Workday, Greenhouse, Lever, and Ashby scrapers:
  - Location filtering (accept/reject patterns)
  - HTML stripping
  - Proxy configuration
"""

import logging
import re
import urllib.request
from html.parser import HTMLParser

log = logging.getLogger(__name__)


# -- Location filtering -----------------------------------------------------

def load_location_filter(search_cfg: dict | None = None) -> tuple[list[str], list[str]]:
    """Load location accept/reject lists from search config.

    Args:
        search_cfg: Search config dict. If None, loads from user config.

    Returns:
        (accept_patterns, reject_patterns) tuple.
    """
    if search_cfg is None:
        from applypilot import config
        search_cfg = config.load_search_config()

    accept = search_cfg.get("location_accept", [])
    reject = search_cfg.get("location_reject_non_remote", [])
    return accept, reject


def location_ok(location: str | None, accept: list[str], reject: list[str]) -> bool:
    """Check if a job location passes the user's location filter.

    Remote jobs are always accepted. Non-remote jobs must match an accept
    pattern and not match a reject pattern.

    Args:
        location: Job location string (may be None).
        accept: Location patterns to accept.
        reject: Location patterns to reject.

    Returns:
        True if the location is acceptable.
    """
    if not location:
        return True

    loc = location.lower()

    # Remote jobs always OK
    if any(r in loc for r in ("remote", "anywhere", "work from home", "wfh", "distributed")):
        return True

    # Reject non-remote matches
    for r in reject:
        if r.lower() in loc:
            return False

    # Accept matches
    for a in accept:
        if a.lower() in loc:
            return True

    # No match -- reject unknown
    return False


# -- HTML stripping ----------------------------------------------------------

class _HTMLStripper(HTMLParser):
    """Strip HTML tags, keep text content."""

    def __init__(self):
        super().__init__()
        self._parts: list[str] = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip = True
        elif tag in ("br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"):
            self._parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = False
        elif tag in ("p", "div", "li", "tr"):
            self._parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self._parts.append(data)

    def get_text(self) -> str:
        text = "".join(self._parts)
        text = re.sub(r"[^\S\n]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def strip_html(html: str) -> str:
    """Convert HTML to plain text."""
    if not html:
        return ""
    stripper = _HTMLStripper()
    stripper.feed(html)
    return stripper.get_text()


# -- Proxy -------------------------------------------------------------------

_opener = None


def setup_proxy(proxy_str: str | None) -> None:
    """Configure a global urllib opener with proxy support."""
    global _opener
    if not proxy_str:
        _opener = urllib.request.build_opener()
        return

    parts = proxy_str.split(":")
    if len(parts) == 4:
        host, port, user, passwd = parts
        proxy_url = f"http://{user}:{passwd}@{host}:{port}"
    elif len(parts) == 2:
        proxy_url = f"http://{parts[0]}:{parts[1]}"
    else:
        log.warning("Proxy format not recognized: %s (expected host:port:user:pass or host:port)", proxy_str)
        _opener = urllib.request.build_opener()
        return

    proxy_handler = urllib.request.ProxyHandler({
        "http": proxy_url,
        "https": proxy_url,
    })
    _opener = urllib.request.build_opener(proxy_handler)
    log.info("Proxy configured: %s:%s", parts[0], parts[1])


def urlopen(req, timeout=30):
    """Open a URL using the configured opener (with or without proxy)."""
    if _opener:
        return _opener.open(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout)


# -- Keyword matching --------------------------------------------------------

def matches_query(text: str, query: str) -> bool:
    """Check if a text (e.g. job title) matches a search query.

    Uses case-insensitive word-level matching. All query words must appear
    somewhere in the text.

    Args:
        text: Text to search in (e.g. job title).
        query: Search query string.

    Returns:
        True if all query words appear in the text.
    """
    if not query or not text:
        return not query  # empty query matches everything

    text_lower = text.lower()
    query_words = query.lower().split()
    return all(word in text_lower for word in query_words)
