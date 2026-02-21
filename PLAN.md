# ApplyPilot Enhancement Plan

## Current State Summary

ApplyPilot is a 6-stage autonomous job application pipeline (discover -> enrich -> score -> tailor -> cover -> pdf -> apply) with:

- **Discovery**: JobSpy (Indeed, LinkedIn, Glassdoor, ZipRecruiter, Google), Workday ATS (48 employers), SmartExtract (30+ career sites)
- **LLM Support**: Gemini (default), OpenAI, local LLM -- all via OpenAI-compatible API
- **No tests**, no Greenhouse support, no Claude/Anthropic support

---

## 1. Greenhouse Job Portal Support

### Background

Greenhouse is one of the most widely-used ATS platforms. Hundreds of tech companies (Airbnb, Stripe, Cloudflare, Discord, Figma, GitLab, HubSpot, etc.) use Greenhouse for job listings. It exposes a **public JSON API** at `https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs` requiring zero authentication -- similar in spirit to the Workday CXS API already implemented.

### Architecture

Follow the existing Workday pattern: create `src/applypilot/discovery/greenhouse.py` as a new discovery strategy registered alongside `jobspy`, `workday`, and `smartextract`.

### Implementation Plan

#### 1a. New file: `src/applypilot/discovery/greenhouse.py`

**API endpoints** (public, no auth):
- List jobs: `GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true`
- Job detail: `GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs/{job_id}`

**Module structure** (mirrors `workday.py`):
```
load_employers()         -> Load Greenhouse employer registry from config/greenhouse.yaml
greenhouse_list_jobs()   -> GET /jobs?content=true (returns all jobs with descriptions)
greenhouse_job_detail()  -> GET /jobs/{id} (full detail with questions)
search_employer()        -> Filter jobs by query keywords + location
fetch_details()          -> Batch detail fetch (descriptions come with list call)
store_results()          -> Insert into jobs table (strategy = "greenhouse_api")
run_greenhouse_discovery() -> Main entry point
```

**Key differences from Workday**:
- Greenhouse returns **all jobs at once** (no pagination needed for most boards, typical board has <500 jobs)
- Descriptions come in the list response when `?content=true` is used, so detail fetching may not need a second call
- Filtering is done client-side: match `title` against search queries using substring/regex matching
- Location filtering reuses existing `_location_ok()` logic from workday.py (extract to shared util)

**Data mapping**:
| Greenhouse field | DB column |
|---|---|
| `absolute_url` | `url` (PK) |
| `title` | `title` |
| `location.name` | `location` |
| `content` (HTML) | `full_description` (after strip_html) |
| `absolute_url` | `application_url` |
| `updated_at` | metadata only |
| `departments[].name` | metadata for filtering |

#### 1b. New config: `src/applypilot/config/greenhouse.yaml`

Registry of Greenhouse board tokens, structured like `employers.yaml`:
```yaml
employers:
  stripe:
    name: "Stripe"
    board_token: "stripe"
  airbnb:
    name: "Airbnb"
    board_token: "airbnb"
  cloudflare:
    name: "Cloudflare"
    board_token: "cloudflare"
  discord:
    name: "Discord"
    board_token: "discord"
  # ... 30+ employers
```

Initial list of Greenhouse employers to include (board tokens are typically the company name in lowercase):
- **Tech**: Stripe, Cloudflare, Discord, Figma, Notion, Airtable, Webflow, Vercel, Supabase, PlanetScale, Linear, Retool, Loom, Miro, Canva, Datadog, HashiCorp, Grafana Labs, dbt Labs, Confluent, Elastic, MongoDB, DigitalOcean, Twilio
- **Fintech**: Plaid, Brex, Ramp, Mercury, Column
- **Other**: Grammarly, Duolingo, Reddit, Pinterest, Snap

#### 1c. Pipeline integration

- Add `greenhouse` call in `pipeline.py:_run_discover()` alongside jobspy, workday, smartextract
- Jobs discovered via Greenhouse arrive **pre-enriched** (full description + apply URL from the API), so they can skip the `enrich` stage. Mark `detail_scraped_at` at discovery time.
- Register strategy `"greenhouse_api"` in the database

#### 1d. Shared utilities refactor

Extract these functions from `workday.py` into a new `src/applypilot/discovery/utils.py`:
- `_location_ok()` -- location accept/reject filtering
- `_load_location_filter()` -- load filter config
- `strip_html()` / `_HTMLStripper` -- HTML to plain text
- `setup_proxy()` / `_urlopen()` -- proxy-aware HTTP

Both `workday.py` and `greenhouse.py` import from `utils.py`. This avoids code duplication without changing any behavior.

---

## 2. Claude/Anthropic Model Support

### Background

The current `llm.py` uses an OpenAI-compatible HTTP client that works with Gemini (via its OpenAI-compat endpoint), OpenAI, and local LLMs. Anthropic's Claude API uses a **different request/response format** (`/v1/messages` with `anthropic-version` header) that is not OpenAI-compatible.

### Implementation Plan

#### 2a. Extend `_detect_provider()` in `src/applypilot/llm.py`

Add a new provider detection path:
```python
_ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

def _detect_provider():
    ...
    if _ANTHROPIC_KEY and not _LOCAL_URL:
        return (
            "https://api.anthropic.com",
            model_override or "claude-sonnet-4-20250514",
            _ANTHROPIC_KEY,
        )
    ...
```

Detection priority order: Gemini -> OpenAI -> **Anthropic** -> Local LLM.

#### 2b. Adapt `LLMClient.chat()` for Anthropic's Messages API

The `chat()` method currently sends OpenAI-format requests. For Anthropic, the request and response format differs:

**OpenAI format** (current):
```json
POST /v1/chat/completions
{"model": "...", "messages": [...], "temperature": 0.0, "max_tokens": 4096}
```

**Anthropic format** (new):
```json
POST /v1/messages
Headers: x-api-key, anthropic-version: 2023-06-01
{"model": "...", "messages": [...], "temperature": 0.0, "max_tokens": 4096}
```

Key differences:
- Endpoint: `/v1/messages` instead of `/chat/completions`
- Auth header: `x-api-key: {key}` instead of `Authorization: Bearer {key}`
- Required header: `anthropic-version: 2023-06-01`
- System message: extracted from messages list into top-level `system` field
- Response: `response["content"][0]["text"]` instead of `response["choices"][0]["message"]["content"]`

**Implementation approach**: Add an `is_anthropic` flag to `LLMClient.__init__()` (detected from base_url containing `anthropic.com`). In `chat()`, branch on this flag for request construction and response parsing. This keeps the change minimal -- no new classes, no new dependencies.

```python
class LLMClient:
    def __init__(self, base_url, model, api_key):
        ...
        self._is_anthropic = "anthropic.com" in base_url

    def chat(self, messages, temperature=0.0, max_tokens=4096):
        if self._is_anthropic:
            return self._chat_anthropic(messages, temperature, max_tokens)
        return self._chat_openai(messages, temperature, max_tokens)
```

#### 2c. Update `.env.example`

Add Anthropic as a documented provider option:
```bash
# ANTHROPIC_API_KEY=       # Claude (claude-sonnet-4-20250514)
```

#### 2d. Update error message in `_detect_provider()`

```python
raise RuntimeError(
    "No LLM provider configured. "
    "Set GEMINI_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY, or LLM_URL."
)
```

#### 2e. Default model selection rationale

- Default to `claude-sonnet-4-20250514` -- best balance of quality and cost for structured output tasks (scoring, resume JSON, cover letter generation)
- Users can override with `LLM_MODEL=claude-opus-4-20250514` for maximum quality or `LLM_MODEL=claude-haiku-4-20250514` for lower cost
- All Claude models handle the structured JSON output required by the tailor and scorer modules

---

## 3. Other Improvements Identified

### 3a. Test Suite (High Priority)

The project has zero tests despite a `pytest` dev dependency. Key areas to test:

- **`llm.py`**: Mock HTTP responses, test provider detection logic, test retry behavior
- **`scorer.py`**: Test `_parse_score_response()` with various LLM output formats
- **`tailor.py`**: Test `extract_json()` with edge cases (fences, preamble, malformed JSON), test `assemble_resume_text()` header injection
- **`cover_letter.py`**: Test `validate_cover_letter()` banned word detection
- **`workday.py`**: Test `_location_ok()`, test `strip_html()`, test `store_results()` dedup
- **`greenhouse.py`** (new): Test JSON parsing, keyword matching, location filtering
- **`database.py`**: Test schema creation, `get_jobs_by_stage()` queries, `ensure_columns()` migration
- **`detail.py`**: Test `extract_from_json_ld()`, test URL resolution

Recommended structure:
```
tests/
  test_llm.py
  test_scorer.py
  test_tailor.py
  test_cover_letter.py
  test_workday.py
  test_greenhouse.py
  test_database.py
  test_detail.py
  conftest.py          # shared fixtures (mock profile, mock resume, mock jobs)
```

### 3b. Lever ATS Support (Medium Priority)

Lever is another major ATS platform (used by Netflix jobs subsidiary, Figma, etc.). Like Greenhouse, it has a public JSON API:
- List: `https://api.lever.co/v0/postings/{company}?mode=json`
- Detail embedded in list response

Could follow the same pattern as Greenhouse. Create `discovery/lever.py` + `config/lever.yaml`.

### 3c. Ashby ATS Support (Medium Priority)

Ashby is a growing ATS used by many startups (Notion, Ramp, etc.). It has a public API:
- `POST https://api.ashbyhq.com/posting-api/job-board/{board_id}`

The codebase already has Ashby CSS selectors in `detail.py` (`ashby-job-posting-apply-button`, `ashby-job-posting-description`), but no direct API integration.

### 3d. Rate Limiting / Token Budget (Medium Priority)

Currently no mechanism to limit LLM spend per run. Consider adding:
- `--max-llm-calls N` CLI flag to cap total LLM invocations per pipeline run
- Token counting (approximate) in `LLMClient` to track spend
- Budget alerts when approaching limits

### 3e. Async HTTP Client (Low Priority)

The LLM client uses synchronous `httpx.Client`. For scoring and tailoring many jobs, switching to `httpx.AsyncClient` with `asyncio.gather()` could improve throughput when using cloud LLM providers that support concurrent requests.

### 3f. Deduplication Improvements (Low Priority)

Current dedup is URL-based only. Jobs from different sources with different URLs but the same posting could be duplicated. Consider:
- Title + company fuzzy matching as secondary dedup
- Normalized company name matching

### 3g. Resume Format Support (Low Priority)

Currently requires plain-text `resume.txt`. Consider accepting:
- PDF input (extract text via PyPDF2/pdfminer)
- DOCX input (extract text via python-docx)
- Markdown input

---

## Implementation Order

| Phase | Work Item | Files Changed |
|---|---|---|
| **Phase 1** | Claude/Anthropic LLM support | `llm.py`, `.env.example` |
| **Phase 2** | Extract shared discovery utilities | New `discovery/utils.py`, update `workday.py` |
| **Phase 3** | Greenhouse portal support | New `discovery/greenhouse.py`, new `config/greenhouse.yaml`, `pipeline.py` |
| **Phase 4** | Test suite foundation | New `tests/` directory with core tests |
| **Phase 5** | Lever + Ashby ATS support (optional) | New `discovery/lever.py`, `discovery/ashby.py` |
