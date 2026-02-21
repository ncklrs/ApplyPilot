"""ElevenLabs TTS integration for personalized pitch audio.

Generates a 30-second voice pitch per job using a cloned voice.
Requires ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID in ~/.applypilot/.env.
"""

import logging
import os
from pathlib import Path

import httpx

from applypilot.config import APP_DIR, load_env, load_profile
from applypilot.llm import get_client

log = logging.getLogger(__name__)

PITCH_DIR = APP_DIR / "pitches"

# ElevenLabs TTS endpoint
_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech"


def _get_elevenlabs_config() -> tuple[str, str]:
    """Load ElevenLabs API key and voice ID from environment.

    Returns:
        (api_key, voice_id)

    Raises:
        ValueError: If either is missing.
    """
    load_env()
    api_key = os.environ.get("ELEVENLABS_API_KEY", "")
    voice_id = os.environ.get("ELEVENLABS_VOICE_ID", "")
    if not api_key:
        raise ValueError(
            "ELEVENLABS_API_KEY not set. Add it to ~/.applypilot/.env"
        )
    if not voice_id:
        raise ValueError(
            "ELEVENLABS_VOICE_ID not set. Add your cloned voice ID to ~/.applypilot/.env"
        )
    return api_key, voice_id


def generate_pitch_script(job: dict, profile: dict) -> str:
    """Generate a 30-second pitch script tailored to a specific job.

    Args:
        job: Job dict with title, site, full_description, fit_score, score_reasoning.
        profile: User profile dict.

    Returns:
        Pitch script text (~80 words, conversational tone).
    """
    personal = profile.get("personal", {})
    experience = profile.get("experience", {})
    resume_facts = profile.get("resume_facts", {})

    name = personal.get("preferred_name") or personal.get("full_name", "")
    years = experience.get("years_of_experience_total", "several")
    projects = resume_facts.get("preserved_projects", [])
    metrics = resume_facts.get("real_metrics", [])

    projects_hint = f"Notable projects: {', '.join(projects)}" if projects else ""
    metrics_hint = f"Real metrics: {', '.join(metrics)}" if metrics else ""

    jd_excerpt = (job.get("full_description") or "")[:2000]
    score_notes = job.get("score_reasoning") or ""

    system_prompt = f"""Write a 30-second voice pitch (~80 words) for {name} applying to a specific job.

TONE: Natural, conversational, confident. Like a voicemail to a hiring manager you respect.
Not formal, not casual. No filler words. No "I'm excited" or "I'm passionate."

STRUCTURE (4 sentences):
1. Greeting + name + what caught your eye about THIS role/company (be specific from the JD)
2. Your most relevant experience (reference a real project or metric)
3. Why your background maps to what they need (connect your work to their challenge)
4. Close: "I put together a quick page at my site showing how my work maps to what you're building. Would love to chat."

RULES:
- Reference something SPECIFIC from the job description (a product, team, technical challenge)
- Include at least one real metric or project name
- Do NOT use: passionate, excited, eager, dynamic, leverage, innovative, cutting-edge
- Do NOT start with "Hi, my name is" — start with "Hey" or "Hi" naturally
- Must sound natural when read aloud. Short sentences. No complex clauses.
{projects_hint}
{metrics_hint}

Output ONLY the pitch text. No labels, no quotes, no commentary."""

    user_prompt = (
        f"JOB TITLE: {job.get('title', 'Software Engineer')}\n"
        f"COMPANY: {job.get('site', 'Unknown')}\n"
        f"FIT SCORE: {job.get('fit_score', 'N/A')}/10\n"
        f"SCORE NOTES: {score_notes[:500]}\n\n"
        f"JOB DESCRIPTION:\n{jd_excerpt}\n\n"
        "Write the 30-second pitch:"
    )

    client = get_client()
    pitch = client.chat(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=256,
        temperature=0.7,
    )
    return pitch.strip()


def synthesize_audio(text: str, output_path: Path) -> Path:
    """Convert text to speech using ElevenLabs with the cloned voice.

    Args:
        text: Pitch script text.
        output_path: Where to save the MP3 file.

    Returns:
        Path to the generated MP3 file.
    """
    api_key, voice_id = _get_elevenlabs_config()

    url = f"{_TTS_URL}/{voice_id}"

    response = httpx.post(
        url,
        headers={
            "xi-api-key": api_key,
            "Content-Type": "application/json",
        },
        json={
            "text": text,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.8,
                "style": 0.3,
            },
        },
        timeout=60,
    )
    response.raise_for_status()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(response.content)
    log.info("Audio saved: %s (%d bytes)", output_path, len(response.content))
    return output_path


def generate_pitch(job: dict, profile: dict | None = None) -> dict:
    """Generate pitch script and audio for a job.

    Args:
        job: Job dict from the database.
        profile: User profile. Loaded from disk if None.

    Returns:
        {"script": str, "audio_path": str, "title": str, "site": str}
    """
    if profile is None:
        profile = load_profile()

    PITCH_DIR.mkdir(parents=True, exist_ok=True)

    # Generate script
    script = generate_pitch_script(job, profile)
    log.info("Pitch script generated for %s @ %s (%d words)",
             job.get("title", "?")[:40], job.get("site", "?"), len(script.split()))

    # Build filename from company + title
    import re
    safe_site = re.sub(r"[^\w\s-]", "", job.get("site", "unknown"))[:20].strip().replace(" ", "_")
    safe_title = re.sub(r"[^\w\s-]", "", job.get("title", "role"))[:40].strip().replace(" ", "_")
    slug = f"{safe_site}_{safe_title}"

    # Save script text
    script_path = PITCH_DIR / f"{slug}_pitch.txt"
    script_path.write_text(script, encoding="utf-8")

    # Synthesize audio
    audio_path = PITCH_DIR / f"{slug}_pitch.mp3"
    try:
        synthesize_audio(script, audio_path)
    except (ValueError, httpx.HTTPStatusError) as e:
        log.warning("Audio synthesis failed (pitch text saved): %s", e)
        return {
            "script": script,
            "audio_path": None,
            "script_path": str(script_path),
            "title": job.get("title", ""),
            "site": job.get("site", ""),
        }

    return {
        "script": script,
        "audio_path": str(audio_path),
        "script_path": str(script_path),
        "title": job.get("title", ""),
        "site": job.get("site", ""),
    }
