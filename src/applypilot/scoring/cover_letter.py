"""Cover letter generation: LLM-powered, profile-driven, with validation.

Generates concise, engineering-voice cover letters tailored to specific job
postings. All personal data (name, skills, achievements) comes from the user's
profile at runtime. No hardcoded personal information.
"""

import json
import logging
import re
import time
from datetime import datetime, timezone

from applypilot.config import COVER_LETTER_DIR, RESUME_PATH, load_profile
from applypilot.database import get_connection, get_jobs_by_stage
from applypilot.llm import get_client
from applypilot.scoring.validator import (
    BANNED_WORDS,
    sanitize_text,
    validate_cover_letter,
)

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 5  # max cross-run retries before giving up


# ── Prompt Builder (profile-driven) ──────────────────────────────────────

def _build_cover_letter_prompt(profile: dict) -> str:
    """Build the cover letter system prompt from the user's profile.

    All personal data, skills, and sign-off name come from the profile.
    """
    personal = profile.get("personal", {})
    boundary = profile.get("skills_boundary", {})
    resume_facts = profile.get("resume_facts", {})

    # Preferred name for the sign-off (falls back to full name)
    sign_off_name = personal.get("preferred_name") or personal.get("full_name", "")

    # Flatten all allowed skills
    all_skills: list[str] = []
    for items in boundary.values():
        if isinstance(items, list):
            all_skills.extend(items)
    skills_str = ", ".join(all_skills) if all_skills else "the tools listed in the resume"

    # Real metrics from resume_facts
    real_metrics = resume_facts.get("real_metrics", [])
    preserved_projects = resume_facts.get("preserved_projects", [])

    # Build achievement examples for the prompt
    projects_hint = ""
    if preserved_projects:
        projects_hint = f"\nKnown projects to reference: {', '.join(preserved_projects)}"

    metrics_hint = ""
    if real_metrics:
        metrics_hint = f"\nReal metrics to use: {', '.join(real_metrics)}"

    return f"""Write a cover letter for {sign_off_name}. The goal is to get an interview.

STRUCTURE: 3 short paragraphs. Under 250 words. Every sentence must earn its place.

PARAGRAPH 1 (2-3 sentences): Open with a specific thing YOU built that solves THEIR problem. Not "I'm excited about this role." Not "This role aligns with my experience." Start with the work.

PARAGRAPH 2 (3-4 sentences): Pick 2 achievements from the resume that are MOST relevant to THIS job. Use numbers. Frame as solving their problem, not listing your accomplishments.{projects_hint}{metrics_hint}

PARAGRAPH 3 (1-2 sentences): One specific thing about the company from the job description (a product, a technical challenge, a team structure). Then close. If a LANDING_PAGE_URL is provided in the job context, end with something like "I put together a quick page at [URL] showing how my work maps to what you're building." Otherwise: "Happy to walk through any of this in more detail." or "Let's discuss." Nothing else.

BANNED WORDS/PHRASES (using ANY of these = instant rejection):
"resonated", "aligns with", "passionate", "eager", "eager to", "excited to apply", "I am confident",
"I believe", "proven track record", "strong track record", "cutting-edge", "innovative", "innovative solutions",
"leverage", "leveraging", "robust", "driven", "dedicated", "committed to",
"I look forward to hearing from you", "great fit", "unique opportunity",
"commitment to excellence", "dynamic team", "fast-paced environment",
"I am writing to express", "caught my eye", "caught my attention"

BANNED PUNCTUATION: No em dashes. Use commas or periods.

VOICE:
- Write like a real engineer emailing someone they respect. Not formal, not casual. Just direct.
- NEVER narrate or explain what you're doing. BAD: "This demonstrates my commitment to X." GOOD: Just state the fact and move on.
- NEVER hedge. BAD: "might address some of your challenges." GOOD: "solves the same problem your team is facing."
- NEVER use "Also," to start a sentence. NEVER use "Furthermore," or "Additionally,".
- Every sentence should contain either a number, a tool name, or a specific outcome. If it doesn't, cut it.
- Read it out loud. If it sounds like a robot wrote it, rewrite it.

ADDITIONAL BANNED PHRASES:
"This demonstrates", "This reflects", "This showcases", "This shows",
"This experience translates", "which aligns with", "which is relevant to",
"as demonstrated by", "showing experience with", "reflecting the need for",
"which directly addresses", "I have experience with",
"Also,", "Furthermore,", "Additionally,", "Moreover,"

FABRICATION = INSTANT REJECTION:
The candidate's real tools are ONLY: {skills_str}.
Do NOT mention ANY tool not in this list. If the job asks for tools not listed, talk about the work you did, not the tools.

Sign off: just "{sign_off_name}"

Output ONLY the letter. Start with "Dear Hiring Manager," end with the name."""


# ── Core Generation ──────────────────────────────────────────────────────

def generate_cover_letter(
    resume_text: str, job: dict, profile: dict, max_retries: int = 3
) -> str:
    """Generate a cover letter with fresh context on each retry + auto-sanitize.

    Same design as tailor_resume: fresh conversation per attempt, issues noted
    in the prompt, no conversation history stacking.

    Args:
        resume_text: The candidate's resume text (base or tailored).
        job: Job dict with title, site, location, full_description.
        profile: User profile dict.
        max_retries: Maximum retry attempts.

    Returns:
        The cover letter text (best attempt even if validation failed).
    """
    landing_url = job.get("landing_page_url") or ""
    landing_line = f"\nLANDING_PAGE_URL: {landing_url}" if landing_url else ""
    job_text = (
        f"TITLE: {job['title']}\n"
        f"COMPANY: {job['site']}\n"
        f"LOCATION: {job.get('location', 'N/A')}\n"
        f"{landing_line}\n"
        f"DESCRIPTION:\n{(job.get('full_description') or '')[:6000]}"
    )

    avoid_notes: list[str] = []
    letter = ""
    client = get_client()
    cl_prompt_base = _build_cover_letter_prompt(profile)

    for attempt in range(max_retries + 1):
        # Fresh conversation every attempt
        prompt = cl_prompt_base
        if avoid_notes:
            prompt += "\n\n## AVOID THESE ISSUES:\n" + "\n".join(
                f"- {n}" for n in avoid_notes[-5:]
            )

        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": (
                f"RESUME:\n{resume_text}\n\n---\n\n"
                f"TARGET JOB:\n{job_text}\n\n"
                "Write the cover letter:"
            )},
        ]

        letter = client.chat(messages, max_tokens=1024, temperature=0.7)
        letter = sanitize_text(letter)  # auto-fix em dashes, smart quotes

        validation = validate_cover_letter(letter)
        if validation["passed"]:
            return letter

        avoid_notes.extend(validation["errors"])
        log.debug(
            "Cover letter attempt %d/%d failed: %s",
            attempt + 1, max_retries + 1, validation["errors"],
        )

    return letter  # last attempt even if failed


# ── Batch Entry Point ────────────────────────────────────────────────────

def run_cover_letters(min_score: int = 7, limit: int = 20,
                      use_agent: bool = False, agent_model: str = "sonnet") -> dict:
    """Generate cover letters for high-scoring jobs that have tailored resumes.

    Args:
        min_score: Minimum fit_score threshold.
        limit: Maximum jobs to process.
        use_agent: If True, use Claude Code agent instead of LLM API.
        agent_model: Claude model name for agent mode.

    Returns:
        {"generated": int, "errors": int, "elapsed": float}
    """
    profile = load_profile()
    resume_text = RESUME_PATH.read_text(encoding="utf-8")
    conn = get_connection()

    # Fetch jobs that have tailored resumes but no cover letter yet
    jobs = conn.execute(
        "SELECT * FROM jobs "
        "WHERE fit_score >= ? AND tailored_resume_path IS NOT NULL "
        "AND full_description IS NOT NULL "
        "AND (cover_letter_path IS NULL OR cover_letter_path = '') "
        "AND COALESCE(cover_attempts, 0) < ? "
        "ORDER BY fit_score DESC LIMIT ?",
        (min_score, MAX_ATTEMPTS, limit),
    ).fetchall()

    if not jobs:
        log.info("No jobs needing cover letters (score >= %d).", min_score)
        return {"generated": 0, "errors": 0, "elapsed": 0.0}

    # Convert rows to dicts
    if jobs and not isinstance(jobs[0], dict):
        columns = jobs[0].keys()
        jobs = [dict(zip(columns, row)) for row in jobs]

    COVER_LETTER_DIR.mkdir(parents=True, exist_ok=True)

    mode = "agent" if use_agent else "LLM API"
    log.info(
        "Generating cover letters for %d jobs (score >= %d, mode=%s)...",
        len(jobs), min_score, mode,
    )
    t0 = time.time()
    completed = 0
    results: list[dict] = []
    error_count = 0

    for job in jobs:
        completed += 1
        try:
            if use_agent:
                from applypilot.scoring.agent import cover_via_agent
                letter = cover_via_agent(resume_text, job, profile, model=agent_model)
            else:
                letter = generate_cover_letter(resume_text, job, profile)

            # Build safe filename prefix
            safe_title = re.sub(r"[^\w\s-]", "", job["title"])[:50].strip().replace(" ", "_")
            safe_site = re.sub(r"[^\w\s-]", "", job["site"])[:20].strip().replace(" ", "_")
            prefix = f"{safe_site}_{safe_title}"

            cl_path = COVER_LETTER_DIR / f"{prefix}_CL.txt"
            cl_path.write_text(letter, encoding="utf-8")

            # Generate PDF (best-effort)
            pdf_path = None
            try:
                from applypilot.scoring.pdf import convert_to_pdf
                pdf_path = str(convert_to_pdf(cl_path))
            except Exception:
                log.debug("PDF generation failed for %s", cl_path, exc_info=True)

            result = {
                "url": job["url"],
                "path": str(cl_path),
                "pdf_path": pdf_path,
                "title": job["title"],
                "site": job["site"],
            }
            results.append(result)

            elapsed = time.time() - t0
            rate = completed / elapsed if elapsed > 0 else 0
            log.info(
                "%d/%d [OK] | %.1f jobs/min | %s",
                completed, len(jobs), rate * 60, result["title"][:40],
            )
        except Exception as e:
            result = {
                "url": job["url"], "title": job["title"], "site": job["site"],
                "path": None, "pdf_path": None, "error": str(e),
            }
            error_count += 1
            results.append(result)
            log.error("%d/%d [ERROR] %s -- %s", completed, len(jobs), job["title"][:40], e)

    # Persist to DB: increment attempt counter for ALL, save path only for successes
    now = datetime.now(timezone.utc).isoformat()
    saved = 0
    for r in results:
        if r.get("path"):
            conn.execute(
                "UPDATE jobs SET cover_letter_path=?, cover_letter_at=?, "
                "cover_attempts=COALESCE(cover_attempts,0)+1 WHERE url=?",
                (r["path"], now, r["url"]),
            )
            saved += 1
        else:
            conn.execute(
                "UPDATE jobs SET cover_attempts=COALESCE(cover_attempts,0)+1 WHERE url=?",
                (r["url"],),
            )
    conn.commit()

    elapsed = time.time() - t0
    log.info("Cover letters done in %.1fs: %d generated, %d errors", elapsed, saved, error_count)

    return {
        "generated": saved,
        "errors": error_count,
        "elapsed": elapsed,
    }
