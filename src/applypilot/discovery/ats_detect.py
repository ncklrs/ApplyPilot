"""ATS auto-detection: probe a company career page to identify which ATS it uses.

Given a URL, this module checks for Greenhouse, Lever, Ashby, and Workday
patterns and returns the ATS type plus the board token / tenant info needed
to add the employer to the appropriate YAML registry.
"""

import json
import logging
import re
import urllib.request
import urllib.error

from applypilot.discovery.utils import urlopen as proxy_urlopen

log = logging.getLogger(__name__)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

# Known ATS URL patterns
_GREENHOUSE_PATTERNS = [
    re.compile(r"boards\.greenhouse\.io/(\w[\w-]*)"),
    re.compile(r"boards-api\.greenhouse\.io/v1/boards/(\w[\w-]*)"),
    re.compile(r'job_board_url.*?greenhouse\.io/(\w[\w-]*)'),
]

_LEVER_PATTERNS = [
    re.compile(r"jobs\.lever\.co/([\w.-]+)"),
    re.compile(r"api\.lever\.co/v0/postings/([\w.-]+)"),
]

_ASHBY_PATTERNS = [
    re.compile(r"jobs\.ashbyhq\.com/([\w.-]+)"),
    re.compile(r"api\.ashbyhq\.com/posting-api/job-board/([\w.-]+)"),
]

_WORKDAY_PATTERNS = [
    re.compile(r"([\w-]+)\.wd(\d+)\.myworkdayjobs\.com"),
    re.compile(r"myworkdayjobs\.com.*?/([\w-]+)"),
]


def _fetch_page(url: str, timeout: int = 15) -> str:
    """Fetch a URL and return its text content."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with proxy_urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        log.debug("Failed to fetch %s: %s", url, e)
        return ""


def _probe_greenhouse(token: str) -> bool:
    """Check if a Greenhouse board token is valid by probing the API."""
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?per_page=1"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with proxy_urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            return "jobs" in data
    except Exception:
        return False


def _probe_lever(slug: str) -> bool:
    """Check if a Lever company slug is valid."""
    url = f"https://api.lever.co/v0/postings/{slug}?limit=1&mode=json"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with proxy_urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            return isinstance(data, list)
    except Exception:
        return False


def _probe_ashby(board_id: str) -> bool:
    """Check if an Ashby board ID is valid."""
    url = f"https://api.ashbyhq.com/posting-api/job-board/{board_id}"
    try:
        data = json.dumps({"operationName": "ApiJobBoardWithTeams"}).encode()
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"User-Agent": UA, "Content-Type": "application/json"},
        )
        with proxy_urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            return "jobs" in result or "data" in result
    except Exception:
        return False


def detect_ats(url: str) -> dict | None:
    """Detect which ATS a company career page uses.

    Checks the URL itself, fetches the page to find embedded ATS links,
    and probes ATS APIs to validate.

    Args:
        url: A company career page URL (e.g. https://example.com/careers).

    Returns:
        Dict with keys: ats, token, name, yaml_snippet, or None if not detected.
        ats is one of: "greenhouse", "lever", "ashby", "workday".
    """
    results = []

    # Phase 1: Check the URL itself for known patterns
    results.extend(_check_patterns(url))

    # Phase 2: Fetch the page and scan HTML for ATS links
    if not results:
        html = _fetch_page(url)
        if html:
            results.extend(_check_patterns(html))

            # Also check for common iframe/redirect patterns
            for link_match in re.finditer(r'href=["\']([^"\']+)["\']', html):
                link = link_match.group(1)
                results.extend(_check_patterns(link))

    # Phase 3: Deduplicate and validate top result
    seen = set()
    for r in results:
        key = (r["ats"], r["token"])
        if key in seen:
            continue
        seen.add(key)

        # Validate by probing the API
        if r["ats"] == "greenhouse" and _probe_greenhouse(r["token"]):
            return _build_result(r["ats"], r["token"], url)
        elif r["ats"] == "lever" and _probe_lever(r["token"]):
            return _build_result(r["ats"], r["token"], url)
        elif r["ats"] == "ashby" and _probe_ashby(r["token"]):
            return _build_result(r["ats"], r["token"], url)
        elif r["ats"] == "workday":
            return _build_result(r["ats"], r["token"], url, extra=r.get("extra"))

    # Phase 4: Try common guesses from domain name
    domain_guess = _guess_from_domain(url)
    if domain_guess:
        return domain_guess

    return None


def _check_patterns(text: str) -> list[dict]:
    """Check text for known ATS URL patterns."""
    results = []

    for pattern in _GREENHOUSE_PATTERNS:
        for m in pattern.finditer(text):
            results.append({"ats": "greenhouse", "token": m.group(1)})

    for pattern in _LEVER_PATTERNS:
        for m in pattern.finditer(text):
            results.append({"ats": "lever", "token": m.group(1)})

    for pattern in _ASHBY_PATTERNS:
        for m in pattern.finditer(text):
            results.append({"ats": "ashby", "token": m.group(1)})

    for pattern in _WORKDAY_PATTERNS:
        for m in pattern.finditer(text):
            tenant = m.group(1)
            wd_num = m.group(2) if pattern.groups > 1 else "5"
            results.append({
                "ats": "workday",
                "token": tenant,
                "extra": {"wd_num": wd_num},
            })

    return results


def _guess_from_domain(url: str) -> dict | None:
    """Try common ATS URL patterns based on the company domain."""
    # Extract domain slug
    domain_match = re.search(r"https?://(?:www\.)?([^./]+)", url)
    if not domain_match:
        return None
    slug = domain_match.group(1).lower()

    # Try Greenhouse
    if _probe_greenhouse(slug):
        return _build_result("greenhouse", slug, url)

    # Try Lever
    if _probe_lever(slug):
        return _build_result("lever", slug, url)

    # Try Ashby
    if _probe_ashby(slug):
        return _build_result("ashby", slug, url)

    return None


def _build_result(ats: str, token: str, url: str, extra: dict | None = None) -> dict:
    """Build a standardized result dict with YAML snippet."""
    # Derive a human-friendly name from the token
    name = token.replace("-", " ").replace("_", " ").title()
    safe_key = re.sub(r"[^a-z0-9_]", "_", token.lower())

    if ats == "greenhouse":
        yaml_snippet = (
            f"  {safe_key}:\n"
            f'    name: "{name}"\n'
            f'    board_token: "{token}"'
        )
        config_file = "config/greenhouse.yaml"
    elif ats == "lever":
        yaml_snippet = (
            f"  {safe_key}:\n"
            f'    name: "{name}"\n'
            f'    company_slug: "{token}"'
        )
        config_file = "config/lever.yaml"
    elif ats == "ashby":
        yaml_snippet = (
            f"  {safe_key}:\n"
            f'    name: "{name}"\n'
            f'    board_id: "{token}"'
        )
        config_file = "config/ashby.yaml"
    elif ats == "workday":
        wd_num = (extra or {}).get("wd_num", "5")
        base = f"https://{token}.wd{wd_num}.myworkdayjobs.com"
        yaml_snippet = (
            f"  {safe_key}:\n"
            f'    name: "{name}"\n'
            f'    tenant: "{token}"\n'
            f'    site_id: "External"\n'
            f'    base_url: "{base}"'
        )
        config_file = "config/employers.yaml"
    else:
        yaml_snippet = ""
        config_file = ""

    return {
        "ats": ats,
        "token": token,
        "name": name,
        "config_file": config_file,
        "yaml_snippet": yaml_snippet,
        "source_url": url,
    }
