# Implementation Plan: High & Medium Priority Features

## Overview
Implementing 5 features: ATS expansion + auto-detect, Claude Code agent mode for tailor/cover, kanban dashboard, email inbox monitor, and HN hidden jobs scraper.

---

## Feature 1: ATS Registry Expansion + Auto-Detection Command (HIGH)

### 1a. Expand YAML registries
Add ~40 more employers across greenhouse.yaml, lever.yaml, ashby.yaml, employers.yaml.

### 1b. `applypilot discover-ats <url>` command
New CLI command that:
1. Takes a company career page URL
2. Probes for known ATS patterns (Greenhouse, Lever, Ashby, Workday)
3. Extracts the board token / tenant info
4. Outputs the YAML snippet to add

**Files to create/modify:**
- `src/applypilot/discovery/ats_detect.py` — new module
- `src/applypilot/cli.py` — add `discover-ats` command
- `src/applypilot/config/*.yaml` — expanded registries

---

## Feature 2: Claude Code Agent Mode for Tailor & Cover (MEDIUM)

Add `--agent` flag to `applypilot run tailor` and `applypilot run cover`.

### Design
- New module: `src/applypilot/scoring/agent.py`
- Reuses subprocess pattern from `apply/launcher.py`
- Falls back to current LLM approach if `claude` CLI not found

**Files to create/modify:**
- `src/applypilot/scoring/agent.py` — new module
- `src/applypilot/scoring/tailor.py` — add agent branch
- `src/applypilot/scoring/cover_letter.py` — add agent branch
- `src/applypilot/cli.py` — add `--agent` and `--model` flags
- `src/applypilot/pipeline.py` — pass through params

---

## Feature 3: Kanban Dashboard (MEDIUM)

Add kanban board view to existing HTML dashboard.

**Files to modify:**
- `src/applypilot/database.py` — add `user_stage`, `user_notes` columns
- `src/applypilot/view.py` — add kanban tab
- `src/applypilot/cli.py` — add `track` command

---

## Feature 4: Email Inbox Monitor (MEDIUM)

New `applypilot inbox` command scanning Gmail via IMAP.

**Files to create/modify:**
- `src/applypilot/inbox.py` — new module
- `src/applypilot/database.py` — add `inbox_status`, `inbox_updated_at` columns
- `src/applypilot/cli.py` — add `inbox` command

---

## Feature 5: HN "Who is Hiring?" Scraper (MEDIUM)

Scrape monthly HN hiring threads via Algolia API.

**Files to create/modify:**
- `src/applypilot/discovery/hn_hiring.py` — new module
- `src/applypilot/pipeline.py` — integrate into discover stage

---

## Execution Order
1. Feature 1 → 2. Feature 5 → 3. Feature 2 → 4. Feature 3 → 5. Feature 4
