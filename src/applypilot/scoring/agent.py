"""Claude Code agent mode for resume tailoring and cover letter generation.

Spawns Claude Code CLI as a subprocess for multi-turn, self-reviewing
resume tailoring and cover letter generation. This produces higher quality
output than single-shot LLM API calls because the agent can read files,
self-review, and iterate.

Reuses the subprocess pattern from apply/launcher.py.
Requires: Claude Code CLI installed (``claude`` on PATH).
"""

import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from applypilot.config import TAILORED_DIR, COVER_LETTER_DIR

log = logging.getLogger(__name__)


def is_agent_available() -> bool:
    """Check if Claude Code CLI is available."""
    return shutil.which("claude") is not None


def _run_claude_agent(
    prompt: str,
    model: str = "sonnet",
    allowed_tools: str = "Read,Write,Edit",
    timeout: int = 180,
    work_dir: str | None = None,
) -> dict:
    """Spawn a Claude Code agent subprocess and collect its output.

    Args:
        prompt: The full prompt to send via stdin.
        model: Claude model name (sonnet, haiku, opus).
        allowed_tools: Comma-separated list of allowed tools.
        timeout: Max seconds to wait.
        work_dir: Working directory for the agent.

    Returns:
        {"status": "ok"|"error", "output": str, "cost_usd": float, "turns": int}
    """
    cmd = [
        "claude",
        "--model", model,
        "-p",
        "--allowedTools", allowed_tools,
        "--permission-mode", "bypassPermissions",
        "--no-session-persistence",
        "--output-format", "json",
        "--verbose", "-",
    ]

    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE_ENTRYPOINT", None)

    cwd = work_dir or tempfile.mkdtemp(prefix="applypilot-agent-")

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            cwd=cwd,
        )

        stdout, stderr = proc.communicate(input=prompt, timeout=timeout)

        if proc.returncode != 0:
            log.warning("Claude agent exited with code %d", proc.returncode)

        # Parse JSON output
        try:
            result = json.loads(stdout) if stdout.strip() else {}
        except json.JSONDecodeError:
            result = {}

        return {
            "status": "ok" if proc.returncode == 0 else "error",
            "output": result.get("result", stdout),
            "cost_usd": result.get("total_cost_usd", 0.0),
            "turns": result.get("num_turns", 0),
            "raw": stdout,
        }

    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        return {
            "status": "error",
            "output": "Agent timed out",
            "cost_usd": 0.0,
            "turns": 0,
            "raw": "",
        }
    except Exception as e:
        return {
            "status": "error",
            "output": str(e),
            "cost_usd": 0.0,
            "turns": 0,
            "raw": "",
        }


def tailor_via_agent(
    resume_text: str,
    job: dict,
    profile: dict,
    model: str = "sonnet",
) -> tuple[str, dict]:
    """Generate a tailored resume using Claude Code agent.

    The agent reads the resume and job description, generates a tailored version,
    self-reviews for fabrication, and outputs the result.

    Args:
        resume_text: Base resume text.
        job: Job dict with title, site, location, full_description.
        profile: User profile dict.
        model: Claude model name.

    Returns:
        (tailored_text, report) matching tailor.py's signature.
    """
    boundary = profile.get("skills_boundary", {})
    resume_facts = profile.get("resume_facts", {})
    personal = profile.get("personal", {})

    # Build skills boundary for the prompt
    skills_lines = []
    for category, items in boundary.items():
        if isinstance(items, list) and items:
            label = category.replace("_", " ").title()
            skills_lines.append(f"{label}: {', '.join(items)}")
    skills_block = "\n".join(skills_lines)

    companies = ", ".join(resume_facts.get("preserved_companies", [])) or "N/A"
    metrics = ", ".join(resume_facts.get("real_metrics", [])) or "N/A"
    school = resume_facts.get("preserved_school", "")

    job_text = (
        f"TITLE: {job['title']}\n"
        f"COMPANY: {job['site']}\n"
        f"LOCATION: {job.get('location', 'N/A')}\n\n"
        f"DESCRIPTION:\n{(job.get('full_description') or '')[:6000]}"
    )

    prompt = f"""You are a resume tailoring agent. Your task is to create a tailored resume.

## INPUTS

### Base Resume:
{resume_text}

### Target Job:
{job_text}

### Skills Boundary (real skills ONLY):
{skills_block}

### Preserved Facts:
- Companies: {companies}
- School: {school}
- Real metrics (do NOT change): {metrics}

## INSTRUCTIONS

1. Read the resume and job description carefully
2. Generate a tailored resume as a JSON object with this schema:
   {{"title": "Role Title", "summary": "2-3 tailored sentences", "skills": {{"Languages": "...", "Frameworks": "...", "DevOps & Infra": "...", "Databases": "...", "Tools": "..."}}, "experience": [{{"header": "Title at Company", "subtitle": "Tech | Dates", "bullets": ["bullet 1", "bullet 2"]}}], "projects": [{{"header": "Project - Description", "subtitle": "Tech | Dates", "bullets": ["bullet 1"]}}], "education": "{school}"}}
3. Self-review: check that no fabricated skills, companies, or inflated metrics appear
4. If you find issues, fix them and output the corrected version

## RULES
- Match the target role title, keep seniority level
- Reorder skills to put job-relevant ones first
- Reframe bullets for relevance -- same real work, different angle
- Strong verb + what you built + quantified impact
- Do NOT invent companies, degrees, tools outside the boundary
- Do NOT use: passionate, dedicated, leveraging, spearheaded, robust, cutting-edge
- Must fit 1 page
- Output ONLY the JSON object. No markdown fences, no commentary."""

    report: dict = {"attempts": 1, "status": "pending", "agent": True}

    result = _run_claude_agent(prompt, model=model)

    if result["status"] == "error":
        report["status"] = "error"
        report["error"] = result["output"]
        return "", report

    # Parse JSON from agent output
    output = result["output"]
    report["cost_usd"] = result["cost_usd"]
    report["turns"] = result["turns"]

    try:
        # Try to extract JSON from the output
        from applypilot.scoring.tailor import extract_json, assemble_resume_text
        data = extract_json(output)
        tailored = assemble_resume_text(data, profile)
        report["status"] = "approved"
        return tailored, report
    except (ValueError, KeyError) as e:
        log.warning("Agent output not valid JSON: %s", e)
        report["status"] = "failed_validation"
        report["error"] = str(e)
        # Return raw output as fallback
        return output if isinstance(output, str) else "", report


def cover_via_agent(
    resume_text: str,
    job: dict,
    profile: dict,
    model: str = "sonnet",
) -> str:
    """Generate a cover letter using Claude Code agent.

    Args:
        resume_text: The candidate's resume text.
        job: Job dict with title, site, location, full_description.
        profile: User profile dict.
        model: Claude model name.

    Returns:
        Cover letter text.
    """
    personal = profile.get("personal", {})
    boundary = profile.get("skills_boundary", {})
    sign_off = personal.get("preferred_name") or personal.get("full_name", "")

    all_skills = []
    for items in boundary.values():
        if isinstance(items, list):
            all_skills.extend(items)
    skills_str = ", ".join(all_skills) or "the tools in the resume"

    job_text = (
        f"TITLE: {job['title']}\n"
        f"COMPANY: {job['site']}\n"
        f"LOCATION: {job.get('location', 'N/A')}\n\n"
        f"DESCRIPTION:\n{(job.get('full_description') or '')[:6000]}"
    )

    prompt = f"""Write a cover letter for {sign_off}. Output ONLY the letter text.

## Resume:
{resume_text}

## Target Job:
{job_text}

## Rules:
- 3 short paragraphs, under 250 words
- Paragraph 1: Open with specific work you built that solves THEIR problem
- Paragraph 2: 2 achievements from resume most relevant to THIS job, with numbers
- Paragraph 3: One specific thing about the company, then close
- Start with "Dear Hiring Manager,"
- End with just "{sign_off}"
- The candidate's real tools are ONLY: {skills_str}. Do NOT mention tools outside this list.
- BANNED words: passionate, eager, excited, leveraging, robust, cutting-edge, innovative, proven track record, I am confident, I believe, aligns with, great fit, I look forward to hearing from you
- No em dashes
- Write like a real engineer, not a robot
- Self-review: make sure no banned words appear, then output the final version
- Output ONLY the cover letter. No commentary, no markdown."""

    result = _run_claude_agent(prompt, model=model)

    if result["status"] == "error":
        log.warning("Cover letter agent failed: %s", result["output"])
        return ""

    output = result["output"]

    # Clean up: remove any markdown fences or preamble
    if isinstance(output, str):
        # Strip markdown fences
        if "```" in output:
            parts = output.split("```")
            if len(parts) >= 3:
                output = parts[1]
                if output.startswith("text"):
                    output = output.removeprefix("text").strip()
                elif output.startswith("\n"):
                    output = output.strip()

        # Find "Dear" if there's preamble
        dear_idx = output.find("Dear")
        if dear_idx > 0:
            output = output[dear_idx:]

        return output.strip()

    return ""
