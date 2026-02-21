"""Personalized landing page generator for job applications.

Generates a self-contained HTML page per job application — a fancy animated
resume personalized to each company. Includes:
  - Audio pitch with waveform player (ElevenLabs TTS)
  - Pitch text displayed alongside audio
  - Short bio
  - Photo (from ~/.applypilot/photo.jpg or photo.png)
  - LinkedIn and GitHub links
  - Skills match visualization
  - Relevant experience highlights
  - Contact CTA

Pages are deployable to nickjensen.codes or subdomain of nickjensen.co.
"""

from __future__ import annotations

import base64
import logging
import re
import time
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from rich.console import Console

from applypilot.config import APP_DIR, load_profile
from applypilot.database import get_connection

log = logging.getLogger(__name__)
console = Console()

LANDING_DIR = APP_DIR / "landing_pages"
PHOTO_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")


def _load_photo_b64() -> tuple[str, str] | tuple[None, None]:
    """Load user photo from APP_DIR as base64 data URI.

    Looks for photo.jpg, photo.jpeg, photo.png, or photo.webp in ~/.applypilot/.

    Returns:
        (data_uri, mime_type) or (None, None) if no photo found.
    """
    for ext in PHOTO_EXTENSIONS:
        photo_path = APP_DIR / f"photo{ext}"
        if photo_path.exists():
            data = photo_path.read_bytes()
            b64 = base64.b64encode(data).decode("ascii")
            mime = {
                ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".png": "image/png", ".webp": "image/webp",
            }[ext]
            return f"data:{mime};base64,{b64}", mime
    return None, None


def _load_audio_b64(audio_path: str | None) -> str | None:
    """Load MP3 audio as base64 data URI for embedding.

    Args:
        audio_path: Path to MP3 file.

    Returns:
        data URI string or None.
    """
    if not audio_path:
        return None
    p = Path(audio_path)
    if not p.exists():
        return None
    data = p.read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:audio/mpeg;base64,{b64}"


def _extract_skills_match(job: dict, profile: dict) -> list[dict]:
    """Extract skills that match between profile and job description.

    Returns list of {"skill": str, "matched": bool} dicts.
    """
    boundary = profile.get("skills_boundary", {})
    all_skills: list[str] = []
    for items in boundary.values():
        if isinstance(items, list):
            all_skills.extend(items)

    jd_lower = (job.get("full_description") or "").lower()
    results = []
    for skill in all_skills:
        matched = skill.lower() in jd_lower
        results.append({"skill": skill, "matched": matched})
    # Sort: matched first
    results.sort(key=lambda x: (not x["matched"], x["skill"]))
    return results


def _make_slug(job: dict) -> str:
    """Generate a URL-safe slug from company name."""
    site = job.get("site", "company")
    slug = re.sub(r"[^\w\s-]", "", site).strip().lower().replace(" ", "-")
    return slug or "company"


def generate_landing_page(
    job: dict,
    pitch_script: str | None = None,
    audio_path: str | None = None,
    profile: dict | None = None,
    base_url: str = "https://nickjensen.codes",
) -> dict:
    """Generate a personalized landing page for a job application.

    Args:
        job: Job dict from the database.
        pitch_script: The 30-second pitch text. If None, uses a generic intro.
        audio_path: Path to MP3 audio file. Embedded as base64 if provided.
        profile: User profile. Loaded from disk if None.
        base_url: Base URL for the deployed page (for canonical links).

    Returns:
        {"path": str, "slug": str, "url": str}
    """
    if profile is None:
        profile = load_profile()

    personal = profile.get("personal", {})
    experience = profile.get("experience", {})
    resume_facts = profile.get("resume_facts", {})

    name = personal.get("preferred_name") or personal.get("full_name", "")
    full_name = personal.get("full_name", name)
    email = personal.get("email", "")
    linkedin = personal.get("linkedin_url", "")
    github = personal.get("github_url", "")
    portfolio = personal.get("portfolio_url", "")
    years = experience.get("years_of_experience_total", "")
    target_role = experience.get("target_role", "Software Engineer")
    education = resume_facts.get("preserved_school", "")
    projects = resume_facts.get("preserved_projects", [])
    metrics = resume_facts.get("real_metrics", [])
    companies = resume_facts.get("preserved_companies", [])

    job_title = job.get("title", "Software Engineer")
    company = job.get("site", "your company")
    fit_score = job.get("fit_score", 0) or 0
    location = job.get("location", "")
    score_reasoning = job.get("score_reasoning", "")

    slug = _make_slug(job)
    page_url = f"{base_url}/{slug}"

    # Load assets
    photo_uri, _ = _load_photo_b64()
    audio_uri = _load_audio_b64(audio_path)

    # Skills match
    skills_data = _extract_skills_match(job, profile)
    matched_count = sum(1 for s in skills_data if s["matched"])
    total_skills = len(skills_data)

    # Build skills HTML
    skills_html = ""
    for s in skills_data[:20]:  # Cap at 20
        cls = "skill-tag matched" if s["matched"] else "skill-tag"
        skills_html += f'<span class="{cls}">{escape(s["skill"])}</span>\n'

    # Photo HTML
    photo_html = ""
    if photo_uri:
        photo_html = f'<img src="{photo_uri}" alt="{escape(name)}" class="hero-photo">'
    else:
        initials = "".join(w[0].upper() for w in full_name.split()[:2]) if full_name else "?"
        photo_html = f'<div class="hero-photo-placeholder">{initials}</div>'

    # Audio player HTML
    audio_html = ""
    if audio_uri:
        audio_html = f"""
        <div class="audio-section">
          <div class="audio-label">30-Second Pitch</div>
          <div class="audio-player">
            <button class="play-btn" onclick="toggleAudio()" id="playBtn">
              <svg id="playIcon" width="24" height="24" viewBox="0 0 24 24" fill="currentColor">
                <polygon points="5,3 19,12 5,21"/>
              </svg>
              <svg id="pauseIcon" width="24" height="24" viewBox="0 0 24 24" fill="currentColor" style="display:none">
                <rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/>
              </svg>
            </button>
            <div class="waveform" id="waveform"></div>
            <span class="audio-time" id="audioTime">0:00</span>
          </div>
          <audio id="pitchAudio" src="{audio_uri}" preload="auto"></audio>
        </div>"""
    elif pitch_script:
        audio_html = '<div class="audio-section"><div class="audio-label">My Pitch</div></div>'

    # Pitch text HTML
    pitch_html = ""
    if pitch_script:
        pitch_html = f"""
        <div class="pitch-text">
          <p>{escape(pitch_script)}</p>
        </div>"""

    # Bio
    bio_parts = []
    if years:
        bio_parts.append(f"{years} years of experience")
    if target_role:
        bio_parts.append(f"specializing in {target_role}")
    if companies:
        bio_parts.append(f"previously at {', '.join(companies[:3])}")
    if education:
        bio_parts.append(f"{education}")
    bio_text = ". ".join(bio_parts).capitalize() + "." if bio_parts else ""

    # Projects HTML
    projects_html = ""
    if projects:
        for proj in projects[:4]:
            projects_html += f"""
            <div class="project-card animate-in">
              <div class="project-name">{escape(proj)}</div>
            </div>"""

    # Metrics HTML
    metrics_html = ""
    if metrics:
        for m in metrics[:4]:
            metrics_html += f'<div class="metric-item animate-in">{escape(m)}</div>\n'

    # Social links
    social_links = ""
    if linkedin:
        social_links += f'<a href="{escape(linkedin)}" class="social-link" target="_blank" rel="noopener">LinkedIn</a>\n'
    if github:
        social_links += f'<a href="{escape(github)}" class="social-link" target="_blank" rel="noopener">GitHub</a>\n'
    if portfolio:
        social_links += f'<a href="{escape(portfolio)}" class="social-link" target="_blank" rel="noopener">Portfolio</a>\n'

    # Score visualization
    score_color = "#10b981" if fit_score >= 7 else ("#f59e0b" if fit_score >= 5 else "#64748b")
    score_pct = fit_score * 10

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{escape(name)} for {escape(job_title)} at {escape(company)}</title>
<meta name="description" content="{escape(name)} — personalized application for {escape(job_title)} at {escape(company)}">
<link rel="canonical" href="{escape(page_url)}">
<style>
  :root {{
    --bg: #0a0f1a;
    --surface: #111827;
    --surface2: #1e293b;
    --border: #1e293b;
    --text: #e2e8f0;
    --text-dim: #94a3b8;
    --text-muted: #64748b;
    --accent: #60a5fa;
    --accent-glow: #60a5fa33;
    --green: #10b981;
    --green-dim: #10b98133;
  }}

  * {{ margin: 0; padding: 0; box-sizing: border-box; }}

  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.6;
    overflow-x: hidden;
  }}

  /* Animated gradient background */
  body::before {{
    content: '';
    position: fixed;
    top: 0; left: 0; right: 0; bottom: 0;
    background:
      radial-gradient(ellipse at 20% 20%, #1e3a5f22 0%, transparent 50%),
      radial-gradient(ellipse at 80% 80%, #10b98111 0%, transparent 50%),
      radial-gradient(ellipse at 50% 50%, #60a5fa08 0%, transparent 70%);
    pointer-events: none;
    z-index: 0;
  }}

  .container {{
    max-width: 800px;
    margin: 0 auto;
    padding: 2rem 1.5rem;
    position: relative;
    z-index: 1;
  }}

  /* Hero Section */
  .hero {{
    text-align: center;
    padding: 3rem 0 2rem;
  }}

  .hero-photo {{
    width: 120px;
    height: 120px;
    border-radius: 50%;
    object-fit: cover;
    border: 3px solid var(--accent);
    box-shadow: 0 0 30px var(--accent-glow);
    margin-bottom: 1.5rem;
  }}

  .hero-photo-placeholder {{
    width: 120px;
    height: 120px;
    border-radius: 50%;
    background: var(--surface2);
    border: 3px solid var(--accent);
    box-shadow: 0 0 30px var(--accent-glow);
    margin: 0 auto 1.5rem;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 2.5rem;
    font-weight: 700;
    color: var(--accent);
  }}

  .hero-name {{
    font-size: 2.2rem;
    font-weight: 700;
    margin-bottom: 0.3rem;
    background: linear-gradient(135deg, var(--text), var(--accent));
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
  }}

  .hero-tagline {{
    font-size: 1.1rem;
    color: var(--text-dim);
    margin-bottom: 0.5rem;
  }}

  .hero-for {{
    display: inline-block;
    font-size: 0.85rem;
    color: var(--green);
    background: var(--green-dim);
    padding: 0.3rem 1rem;
    border-radius: 20px;
    font-weight: 500;
  }}

  /* Social links row */
  .social-row {{
    display: flex;
    justify-content: center;
    gap: 0.75rem;
    margin-top: 1.5rem;
  }}

  .social-link {{
    color: var(--accent);
    text-decoration: none;
    font-size: 0.85rem;
    padding: 0.4rem 1rem;
    border: 1px solid var(--accent);
    border-radius: 8px;
    transition: all 0.2s;
  }}

  .social-link:hover {{
    background: var(--accent);
    color: var(--bg);
  }}

  /* Section cards */
  .section {{
    background: var(--surface);
    border-radius: 16px;
    padding: 1.5rem;
    margin-bottom: 1.5rem;
    border: 1px solid var(--border);
    transition: transform 0.2s, box-shadow 0.2s;
  }}

  .section:hover {{
    transform: translateY(-2px);
    box-shadow: 0 8px 24px #00000044;
  }}

  .section-label {{
    font-size: 0.72rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--text-muted);
    margin-bottom: 0.75rem;
  }}

  /* Audio player */
  .audio-section {{
    margin-bottom: 0.75rem;
  }}

  .audio-label {{
    font-size: 0.72rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--text-muted);
    margin-bottom: 0.5rem;
  }}

  .audio-player {{
    display: flex;
    align-items: center;
    gap: 0.75rem;
    background: var(--bg);
    border-radius: 12px;
    padding: 0.75rem 1rem;
  }}

  .play-btn {{
    background: var(--accent);
    border: none;
    border-radius: 50%;
    width: 40px;
    height: 40px;
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    color: var(--bg);
    flex-shrink: 0;
    transition: transform 0.15s, box-shadow 0.15s;
  }}

  .play-btn:hover {{
    transform: scale(1.1);
    box-shadow: 0 0 16px var(--accent-glow);
  }}

  .waveform {{
    flex: 1;
    height: 40px;
    display: flex;
    align-items: center;
    gap: 2px;
  }}

  .waveform .bar {{
    width: 3px;
    background: var(--accent);
    border-radius: 2px;
    transition: height 0.15s;
    opacity: 0.4;
  }}

  .waveform .bar.active {{
    opacity: 1;
  }}

  .audio-time {{
    font-size: 0.8rem;
    color: var(--text-muted);
    font-variant-numeric: tabular-nums;
    min-width: 3rem;
    text-align: right;
  }}

  /* Pitch text */
  .pitch-text {{
    margin-top: 0.75rem;
  }}

  .pitch-text p {{
    font-size: 0.95rem;
    color: var(--text-dim);
    font-style: italic;
    line-height: 1.7;
    border-left: 3px solid var(--accent);
    padding-left: 1rem;
  }}

  /* Bio */
  .bio-text {{
    font-size: 0.95rem;
    line-height: 1.7;
    color: var(--text);
  }}

  /* Skills */
  .skills-header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 0.75rem;
  }}

  .skills-match-count {{
    font-size: 0.8rem;
    color: var(--green);
    font-weight: 600;
  }}

  .skills-grid {{
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
  }}

  .skill-tag {{
    font-size: 0.78rem;
    padding: 0.25rem 0.65rem;
    border-radius: 6px;
    background: var(--surface2);
    color: var(--text-muted);
    border: 1px solid var(--border);
    transition: all 0.2s;
  }}

  .skill-tag.matched {{
    background: var(--green-dim);
    color: var(--green);
    border-color: #10b98144;
    font-weight: 500;
  }}

  /* Fit score ring */
  .score-section {{
    display: flex;
    align-items: center;
    gap: 1.5rem;
  }}

  .score-ring {{
    position: relative;
    width: 80px;
    height: 80px;
    flex-shrink: 0;
  }}

  .score-ring svg {{
    transform: rotate(-90deg);
  }}

  .score-ring-bg {{
    fill: none;
    stroke: var(--surface2);
    stroke-width: 6;
  }}

  .score-ring-fill {{
    fill: none;
    stroke: {score_color};
    stroke-width: 6;
    stroke-linecap: round;
    stroke-dasharray: 226;
    stroke-dashoffset: {226 - (226 * score_pct / 100)};
    transition: stroke-dashoffset 1.5s ease-out;
  }}

  .score-ring-text {{
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    font-size: 1.5rem;
    font-weight: 700;
    color: {score_color};
  }}

  .score-details {{
    flex: 1;
  }}

  .score-label-text {{
    font-size: 1rem;
    font-weight: 600;
    color: var(--text);
    margin-bottom: 0.25rem;
  }}

  .score-reasoning {{
    font-size: 0.82rem;
    color: var(--text-dim);
    line-height: 1.5;
  }}

  /* Projects */
  .projects-grid {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0.75rem;
  }}

  .project-card {{
    background: var(--bg);
    border-radius: 10px;
    padding: 1rem;
    border: 1px solid var(--border);
    transition: border-color 0.2s;
  }}

  .project-card:hover {{
    border-color: var(--accent);
  }}

  .project-name {{
    font-weight: 600;
    font-size: 0.9rem;
    color: var(--text);
  }}

  /* Metrics */
  .metrics-grid {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0.75rem;
  }}

  .metric-item {{
    background: var(--bg);
    border-radius: 10px;
    padding: 0.75rem 1rem;
    font-size: 0.9rem;
    color: var(--green);
    font-weight: 500;
    text-align: center;
    border: 1px solid var(--green-dim);
  }}

  /* CTA */
  .cta-section {{
    text-align: center;
    padding: 2rem 0;
  }}

  .cta-button {{
    display: inline-block;
    background: var(--accent);
    color: var(--bg);
    padding: 0.8rem 2.5rem;
    border-radius: 10px;
    text-decoration: none;
    font-weight: 600;
    font-size: 1rem;
    transition: transform 0.2s, box-shadow 0.2s;
  }}

  .cta-button:hover {{
    transform: translateY(-2px);
    box-shadow: 0 8px 24px var(--accent-glow);
  }}

  .cta-sub {{
    margin-top: 0.75rem;
    font-size: 0.82rem;
    color: var(--text-muted);
  }}

  /* Footer */
  .footer {{
    text-align: center;
    padding: 2rem 0 1rem;
    font-size: 0.75rem;
    color: var(--text-muted);
  }}

  /* Animations */
  .animate-in {{
    opacity: 0;
    transform: translateY(20px);
    transition: opacity 0.6s ease-out, transform 0.6s ease-out;
  }}

  .animate-in.visible {{
    opacity: 1;
    transform: translateY(0);
  }}

  /* Responsive */
  @media (max-width: 640px) {{
    .container {{ padding: 1rem; }}
    .hero-name {{ font-size: 1.6rem; }}
    .projects-grid {{ grid-template-columns: 1fr; }}
    .metrics-grid {{ grid-template-columns: 1fr; }}
    .score-section {{ flex-direction: column; text-align: center; }}
  }}
</style>
</head>
<body>

<div class="container">

  <!-- Hero -->
  <div class="hero animate-in">
    {photo_html}
    <h1 class="hero-name">{escape(full_name)}</h1>
    <p class="hero-tagline">{escape(target_role.title())}{(' | ' + escape(years) + ' years') if years else ''}</p>
    <span class="hero-for">for {escape(job_title)} at {escape(company)}</span>
    <div class="social-row">
      {social_links}
      <a href="mailto:{escape(email)}" class="social-link">Email</a>
    </div>
  </div>

  <!-- Pitch (Audio + Text) -->
  <div class="section animate-in">
    {audio_html}
    {pitch_html}
  </div>

  <!-- Bio -->
  <div class="section animate-in">
    <div class="section-label">About</div>
    <p class="bio-text">{escape(bio_text)}</p>
  </div>

  <!-- Fit Score -->
  <div class="section animate-in">
    <div class="section-label">Fit Score</div>
    <div class="score-section">
      <div class="score-ring">
        <svg width="80" height="80" viewBox="0 0 80 80">
          <circle class="score-ring-bg" cx="40" cy="40" r="36"/>
          <circle class="score-ring-fill" cx="40" cy="40" r="36"/>
        </svg>
        <div class="score-ring-text">{fit_score}</div>
      </div>
      <div class="score-details">
        <div class="score-label-text">{'Strong Match' if fit_score >= 7 else ('Good Match' if fit_score >= 5 else 'Match')}</div>
        <p class="score-reasoning">{escape((score_reasoning or '').split(chr(10))[0][:200])}</p>
      </div>
    </div>
  </div>

  <!-- Skills Match -->
  <div class="section animate-in">
    <div class="skills-header">
      <div class="section-label">Skills</div>
      <span class="skills-match-count">{matched_count}/{total_skills} match this role</span>
    </div>
    <div class="skills-grid">
      {skills_html}
    </div>
  </div>

  <!-- Metrics -->
  {'<div class="section animate-in"><div class="section-label">Impact</div><div class="metrics-grid">' + metrics_html + '</div></div>' if metrics_html else ''}

  <!-- Projects -->
  {'<div class="section animate-in"><div class="section-label">Projects</div><div class="projects-grid">' + projects_html + '</div></div>' if projects_html else ''}

  <!-- CTA -->
  <div class="cta-section animate-in">
    <a href="mailto:{escape(email)}?subject={escape(f'Re: {job_title} at {company}')}" class="cta-button">Let's Talk</a>
    <p class="cta-sub">{escape(email)}</p>
  </div>

  <div class="footer">
    Built with ApplyPilot
  </div>

</div>

<script>
// Intersection Observer for scroll animations
const observer = new IntersectionObserver((entries) => {{
  entries.forEach(entry => {{
    if (entry.isIntersecting) {{
      entry.target.classList.add('visible');
    }}
  }});
}}, {{ threshold: 0.1 }});

document.querySelectorAll('.animate-in').forEach(el => observer.observe(el));

// Audio player
const audio = document.getElementById('pitchAudio');
const playBtn = document.getElementById('playBtn');
const playIcon = document.getElementById('playIcon');
const pauseIcon = document.getElementById('pauseIcon');
const timeDisplay = document.getElementById('audioTime');
const waveformEl = document.getElementById('waveform');

// Generate waveform bars
if (waveformEl) {{
  const barCount = 60;
  for (let i = 0; i < barCount; i++) {{
    const bar = document.createElement('div');
    bar.className = 'bar';
    const h = 8 + Math.random() * 28;
    bar.style.height = h + 'px';
    waveformEl.appendChild(bar);
  }}
}}

function toggleAudio() {{
  if (!audio) return;
  if (audio.paused) {{
    audio.play();
    playIcon.style.display = 'none';
    pauseIcon.style.display = 'block';
  }} else {{
    audio.pause();
    playIcon.style.display = 'block';
    pauseIcon.style.display = 'none';
  }}
}}

if (audio) {{
  audio.addEventListener('timeupdate', () => {{
    const pct = audio.currentTime / (audio.duration || 1);
    const bars = waveformEl ? waveformEl.querySelectorAll('.bar') : [];
    const activeIdx = Math.floor(pct * bars.length);
    bars.forEach((bar, i) => {{
      bar.classList.toggle('active', i <= activeIdx);
    }});
    const mins = Math.floor(audio.currentTime / 60);
    const secs = Math.floor(audio.currentTime % 60);
    if (timeDisplay) timeDisplay.textContent = mins + ':' + String(secs).padStart(2, '0');
  }});

  audio.addEventListener('ended', () => {{
    playIcon.style.display = 'block';
    pauseIcon.style.display = 'none';
    const bars = waveformEl ? waveformEl.querySelectorAll('.bar') : [];
    bars.forEach(bar => bar.classList.remove('active'));
    if (timeDisplay) timeDisplay.textContent = '0:00';
  }});
}}
</script>

</body>
</html>"""

    # Write file
    LANDING_DIR.mkdir(parents=True, exist_ok=True)
    slug_dir = LANDING_DIR / slug
    slug_dir.mkdir(parents=True, exist_ok=True)
    out_path = slug_dir / "index.html"
    out_path.write_text(html, encoding="utf-8")

    log.info("Landing page generated: %s", out_path)
    return {
        "path": str(out_path),
        "slug": slug,
        "url": page_url,
    }


def run_landing_pages(min_score: int = 7, limit: int = 20) -> dict:
    """Generate landing pages for high-scoring jobs with cover letters.

    Args:
        min_score: Minimum fit_score threshold.
        limit: Maximum jobs to process.

    Returns:
        {"generated": int, "errors": int, "elapsed": float}
    """
    from applypilot.voice import generate_pitch

    profile = load_profile()
    conn = get_connection()

    # Jobs that have cover letters but no landing page yet
    jobs = conn.execute(
        "SELECT * FROM jobs "
        "WHERE fit_score >= ? AND cover_letter_path IS NOT NULL "
        "AND full_description IS NOT NULL "
        "AND (landing_page_path IS NULL OR landing_page_path = '') "
        "ORDER BY fit_score DESC LIMIT ?",
        (min_score, limit),
    ).fetchall()

    if not jobs:
        log.info("No jobs needing landing pages (score >= %d).", min_score)
        return {"generated": 0, "errors": 0, "elapsed": 0.0}

    # Convert to dicts
    if jobs and not isinstance(jobs[0], dict):
        columns = jobs[0].keys()
        jobs = [dict(zip(columns, row)) for row in jobs]

    log.info("Generating landing pages for %d jobs (score >= %d)...", len(jobs), min_score)
    t0 = time.time()
    generated = 0
    errors = 0

    for job in jobs:
        try:
            # Generate pitch (script + audio)
            pitch_result = generate_pitch(job, profile)
            pitch_script = pitch_result.get("script")
            audio_path = pitch_result.get("audio_path")

            # Generate landing page
            page = generate_landing_page(
                job,
                pitch_script=pitch_script,
                audio_path=audio_path,
                profile=profile,
            )

            # Update DB
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE jobs SET landing_page_path=?, landing_page_url=?, "
                "pitch_script=?, pitch_audio_path=?, landing_page_at=? WHERE url=?",
                (page["path"], page["url"], pitch_script, audio_path, now, job["url"]),
            )
            conn.commit()
            generated += 1

            elapsed = time.time() - t0
            rate = generated / elapsed if elapsed > 0 else 0
            log.info(
                "%d/%d [OK] | %.1f/min | %s @ %s -> %s",
                generated, len(jobs), rate * 60,
                job["title"][:30], job["site"][:20], page["slug"],
            )

        except Exception as e:
            errors += 1
            log.error("Landing page failed for %s: %s", job.get("title", "?")[:40], e)

    elapsed = time.time() - t0
    log.info("Landing pages done in %.1fs: %d generated, %d errors", elapsed, generated, errors)
    return {"generated": generated, "errors": errors, "elapsed": elapsed}
