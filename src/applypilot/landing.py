"""Personalized landing page generator for job applications.

Generates a React + Tailwind page per job application — a premium animated
resume personalized to each company. Includes:
  - Audio pitch with waveform player (ElevenLabs TTS)
  - Pitch text displayed alongside audio
  - Short bio, photo, LinkedIn/GitHub links
  - Skills match visualization
  - Fit score ring with reasoning
  - Impact metrics + projects
  - Contact CTA

Pages deploy to hire.nickjensen.co/{company-slug} via GitHub Pages.
"""

from __future__ import annotations

import base64
import json
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
    """Generate a URL-safe slug from company name only."""
    site = job.get("site", "company")
    slug = re.sub(r"[^\w\s-]", "", site).strip().lower().replace(" ", "-")
    return (slug or "company")[:60]


def generate_landing_page(
    job: dict,
    pitch_script: str | None = None,
    audio_path: str | None = None,
    profile: dict | None = None,
    base_url: str = "https://hire.nickjensen.co",
) -> dict:
    """Generate a personalized landing page for a job application.

    Uses React (CDN) + Tailwind CSS (CDN) for a polished, animated page.
    Output is a self-contained HTML file with all assets embedded.

    Args:
        job: Job dict from the database.
        pitch_script: The 30-second pitch text. If None, section is hidden.
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

    full_name = personal.get("full_name", "")
    name = personal.get("preferred_name") or full_name
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

    # Initials fallback
    initials = "".join(w[0].upper() for w in full_name.split()[:2]) if full_name else "?"

    # Score label
    if fit_score >= 8:
        score_label = "Strong Match"
    elif fit_score >= 6:
        score_label = "Good Match"
    else:
        score_label = "Match"

    # Build page data as JSON for React to consume
    page_data = {
        "name": name,
        "fullName": full_name,
        "initials": initials,
        "email": email,
        "linkedin": linkedin,
        "github": github,
        "portfolio": portfolio,
        "bio": bio_text,
        "targetRole": target_role.title() if target_role else "",
        "years": years,
        "jobTitle": job_title,
        "company": company,
        "location": location,
        "fitScore": fit_score,
        "scoreLabel": score_label,
        "scoreReasoning": (score_reasoning or "").split("\n")[0][:200],
        "skills": skills_data[:20],
        "matchedSkills": matched_count,
        "totalSkills": total_skills,
        "projects": projects[:4],
        "metrics": metrics[:4],
        "pitchScript": pitch_script,
        "photoUri": photo_uri,
        "audioUri": audio_uri,
    }

    # Escape for safe embedding in <script> tag
    data_json = json.dumps(page_data, ensure_ascii=True)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{escape(full_name)} for {escape(job_title)} at {escape(company)}</title>
<meta name="description" content="{escape(full_name)} &mdash; personalized application for {escape(job_title)} at {escape(company)}">
<link rel="canonical" href="{escape(page_url)}">
<script src="https://cdn.tailwindcss.com"></script>
<script>
tailwind.config = {{
  theme: {{
    extend: {{
      colors: {{
        bg: '#0a0f1a',
        surface: '#111827',
        surface2: '#1e293b',
        border: '#1e293b',
        dim: '#94a3b8',
        muted: '#64748b',
        accent: '#60a5fa',
        accentGlow: 'rgba(96,165,250,0.2)',
        green: '#10b981',
        greenDim: 'rgba(16,185,129,0.2)',
      }},
      fontFamily: {{
        sans: ['-apple-system','BlinkMacSystemFont','Segoe UI','system-ui','sans-serif'],
      }},
    }}
  }}
}}
</script>
<script crossorigin src="https://unpkg.com/react@18/umd/react.production.min.js"></script>
<script crossorigin src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>
<script src="https://unpkg.com/@babel/standalone/babel.min.js"></script>
<style>
  body {{ background: #0a0f1a; margin: 0; }}
  body::before {{
    content: '';
    position: fixed;
    inset: 0;
    background:
      radial-gradient(ellipse at 20% 20%, rgba(30,58,95,0.13) 0%, transparent 50%),
      radial-gradient(ellipse at 80% 80%, rgba(16,185,129,0.07) 0%, transparent 50%),
      radial-gradient(ellipse at 50% 50%, rgba(96,165,250,0.03) 0%, transparent 70%);
    pointer-events: none;
    z-index: 0;
  }}
  .fade-in {{ opacity: 0; transform: translateY(24px); transition: opacity 0.7s ease-out, transform 0.7s ease-out; }}
  .fade-in.visible {{ opacity: 1; transform: translateY(0); }}
  .glass {{
    background: rgba(17,24,39,0.8);
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
    border: 1px solid rgba(30,41,59,0.6);
  }}
  .waveform-bar {{
    width: 3px;
    border-radius: 2px;
    background: #60a5fa;
    opacity: 0.3;
    transition: height 0.15s, opacity 0.15s;
  }}
  .waveform-bar.active {{ opacity: 1; }}
  .score-ring-fill {{
    transition: stroke-dashoffset 1.5s ease-out;
  }}
  @keyframes pulse-glow {{
    0%, 100% {{ box-shadow: 0 0 20px rgba(96,165,250,0.15); }}
    50% {{ box-shadow: 0 0 35px rgba(96,165,250,0.3); }}
  }}
  .photo-glow {{ animation: pulse-glow 3s ease-in-out infinite; }}
</style>
</head>
<body>
<div id="root"></div>

<script type="text/babel" data-type="module">
const DATA = {data_json};

const {{ useState, useEffect, useRef, useCallback }} = React;

/* ── Intersection Observer hook ── */
function useFadeIn() {{
  const ref = useRef(null);
  useEffect(() => {{
    const el = ref.current;
    if (!el) return;
    const obs = new IntersectionObserver(
      ([entry]) => {{ if (entry.isIntersecting) {{ el.classList.add('visible'); obs.unobserve(el); }} }},
      {{ threshold: 0.1 }}
    );
    obs.observe(el);
    return () => obs.disconnect();
  }}, []);
  return ref;
}}

function Section({{ children, className = '' }}) {{
  const ref = useFadeIn();
  return (
    <div ref={{ref}} className={{"fade-in glass rounded-2xl p-6 mb-5 hover:translate-y-[-2px] hover:shadow-xl transition-all duration-200 " + className}}>
      {{children}}
    </div>
  );
}}

function SectionLabel({{ children }}) {{
  return <div className="text-[0.7rem] font-semibold uppercase tracking-widest text-muted mb-3">{{children}}</div>;
}}

/* ── Hero ── */
function Hero() {{
  const ref = useFadeIn();
  return (
    <div ref={{ref}} className="fade-in text-center pt-12 pb-8">
      {{DATA.photoUri ? (
        <img src={{DATA.photoUri}} alt={{DATA.name}}
          className="w-28 h-28 rounded-full object-cover border-[3px] border-accent photo-glow mx-auto mb-5" />
      ) : (
        <div className="w-28 h-28 rounded-full bg-surface2 border-[3px] border-accent photo-glow mx-auto mb-5 flex items-center justify-center text-4xl font-bold text-accent">
          {{DATA.initials}}
        </div>
      )}}
      <h1 className="text-4xl font-bold mb-1 bg-gradient-to-r from-slate-200 to-accent bg-clip-text text-transparent">
        {{DATA.fullName}}
      </h1>
      <p className="text-lg text-dim mb-2">
        {{DATA.targetRole}}{{DATA.years ? ` · ${{DATA.years}} years` : ''}}
      </p>
      <span className="inline-block text-sm text-green bg-greenDim px-4 py-1 rounded-full font-medium">
        for {{DATA.jobTitle}} at {{DATA.company}}
      </span>

      <div className="flex justify-center gap-3 mt-5">
        {{DATA.linkedin && <a href={{DATA.linkedin}} target="_blank" rel="noopener"
          className="text-sm text-accent border border-accent px-4 py-1.5 rounded-lg hover:bg-accent hover:text-bg transition-colors">LinkedIn</a>}}
        {{DATA.github && <a href={{DATA.github}} target="_blank" rel="noopener"
          className="text-sm text-accent border border-accent px-4 py-1.5 rounded-lg hover:bg-accent hover:text-bg transition-colors">GitHub</a>}}
        {{DATA.portfolio && <a href={{DATA.portfolio}} target="_blank" rel="noopener"
          className="text-sm text-accent border border-accent px-4 py-1.5 rounded-lg hover:bg-accent hover:text-bg transition-colors">Portfolio</a>}}
        <a href={{"mailto:" + DATA.email}}
          className="text-sm text-accent border border-accent px-4 py-1.5 rounded-lg hover:bg-accent hover:text-bg transition-colors">Email</a>
      </div>
    </div>
  );
}}

/* ── Audio Player ── */
function AudioPlayer() {{
  const audioRef = useRef(null);
  const waveRef = useRef(null);
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState('0:00');

  useEffect(() => {{
    if (!waveRef.current) return;
    const bars = 55;
    for (let i = 0; i < bars; i++) {{
      const bar = document.createElement('div');
      bar.className = 'waveform-bar';
      bar.style.height = (6 + Math.random() * 30) + 'px';
      waveRef.current.appendChild(bar);
    }}
  }}, []);

  const toggle = useCallback(() => {{
    const a = audioRef.current;
    if (!a) return;
    if (a.paused) {{ a.play(); setPlaying(true); }}
    else {{ a.pause(); setPlaying(false); }}
  }}, []);

  useEffect(() => {{
    const a = audioRef.current;
    if (!a) return;
    const onTime = () => {{
      const pct = a.currentTime / (a.duration || 1);
      const bars = waveRef.current?.querySelectorAll('.waveform-bar') || [];
      const idx = Math.floor(pct * bars.length);
      bars.forEach((b, i) => b.classList.toggle('active', i <= idx));
      const m = Math.floor(a.currentTime / 60);
      const s = Math.floor(a.currentTime % 60);
      setTime(m + ':' + String(s).padStart(2, '0'));
    }};
    const onEnd = () => {{
      setPlaying(false);
      setTime('0:00');
      const bars = waveRef.current?.querySelectorAll('.waveform-bar') || [];
      bars.forEach(b => b.classList.remove('active'));
    }};
    a.addEventListener('timeupdate', onTime);
    a.addEventListener('ended', onEnd);
    return () => {{ a.removeEventListener('timeupdate', onTime); a.removeEventListener('ended', onEnd); }};
  }}, []);

  if (!DATA.audioUri && !DATA.pitchScript) return null;

  return (
    <Section>
      {{DATA.audioUri && (
        <>
          <SectionLabel>30-Second Pitch</SectionLabel>
          <div className="flex items-center gap-3 bg-bg rounded-xl p-3">
            <button onClick={{toggle}}
              className="w-10 h-10 rounded-full bg-accent flex items-center justify-center flex-shrink-0 hover:scale-110 hover:shadow-lg hover:shadow-accent/20 transition-all">
              {{playing ? (
                <svg width="18" height="18" viewBox="0 0 24 24" fill="#0a0f1a"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>
              ) : (
                <svg width="18" height="18" viewBox="0 0 24 24" fill="#0a0f1a"><polygon points="6,3 20,12 6,21"/></svg>
              )}}
            </button>
            <div ref={{waveRef}} className="flex-1 h-10 flex items-center gap-[2px]"></div>
            <span className="text-sm text-muted tabular-nums min-w-[3rem] text-right">{{time}}</span>
          </div>
          <audio ref={{audioRef}} src={{DATA.audioUri}} preload="auto" />
        </>
      )}}
      {{DATA.pitchScript && (
        <div className="mt-3">
          <p className="text-[0.93rem] text-dim italic leading-relaxed border-l-[3px] border-accent pl-4">
            {{DATA.pitchScript}}
          </p>
        </div>
      )}}
    </Section>
  );
}}

/* ── Bio ── */
function Bio() {{
  if (!DATA.bio) return null;
  return (
    <Section>
      <SectionLabel>About</SectionLabel>
      <p className="text-[0.95rem] leading-relaxed text-slate-200">{{DATA.bio}}</p>
    </Section>
  );
}}

/* ── Fit Score ── */
function FitScore() {{
  const scoreColor = DATA.fitScore >= 7 ? '#10b981' : (DATA.fitScore >= 5 ? '#f59e0b' : '#64748b');
  const pct = DATA.fitScore * 10;
  const dashOffset = 226 - (226 * pct / 100);

  return (
    <Section>
      <SectionLabel>Fit Score</SectionLabel>
      <div className="flex items-center gap-6 sm:flex-row flex-col">
        <div className="relative w-20 h-20 flex-shrink-0">
          <svg width="80" height="80" viewBox="0 0 80 80" style={{{{ transform: 'rotate(-90deg)' }}}}>
            <circle cx="40" cy="40" r="36" fill="none" stroke="#1e293b" strokeWidth="6" />
            <circle className="score-ring-fill" cx="40" cy="40" r="36" fill="none"
              stroke={{scoreColor}} strokeWidth="6" strokeLinecap="round"
              strokeDasharray="226" strokeDashoffset={{dashOffset}} />
          </svg>
          <div className="absolute inset-0 flex items-center justify-center text-2xl font-bold"
            style={{{{ color: scoreColor }}}}>{{DATA.fitScore}}</div>
        </div>
        <div className="flex-1 text-center sm:text-left">
          <div className="font-semibold text-slate-200 mb-1">{{DATA.scoreLabel}}</div>
          <p className="text-sm text-dim leading-relaxed">{{DATA.scoreReasoning}}</p>
        </div>
      </div>
    </Section>
  );
}}

/* ── Skills ── */
function Skills() {{
  if (DATA.skills.length === 0) return null;
  return (
    <Section>
      <div className="flex justify-between items-center mb-3">
        <SectionLabel>Skills</SectionLabel>
        <span className="text-sm text-green font-semibold">{{DATA.matchedSkills}}/{{DATA.totalSkills}} match</span>
      </div>
      <div className="flex flex-wrap gap-2">
        {{DATA.skills.map((s, i) => (
          <span key={{i}} className={{
            s.matched
              ? "text-[0.78rem] px-3 py-1 rounded-md bg-greenDim text-green border border-green/25 font-medium"
              : "text-[0.78rem] px-3 py-1 rounded-md bg-surface2 text-muted border border-border"
          }}>{{s.skill}}</span>
        ))}}
      </div>
    </Section>
  );
}}

/* ── Metrics ── */
function Metrics() {{
  if (DATA.metrics.length === 0) return null;
  return (
    <Section>
      <SectionLabel>Impact</SectionLabel>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {{DATA.metrics.map((m, i) => (
          <div key={{i}} className="bg-bg rounded-xl p-4 text-center text-green font-medium text-[0.9rem] border border-greenDim">
            {{m}}
          </div>
        ))}}
      </div>
    </Section>
  );
}}

/* ── Projects ── */
function Projects() {{
  if (DATA.projects.length === 0) return null;
  return (
    <Section>
      <SectionLabel>Projects</SectionLabel>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {{DATA.projects.map((p, i) => (
          <div key={{i}} className="bg-bg rounded-xl p-4 border border-border hover:border-accent transition-colors">
            <div className="font-semibold text-[0.9rem] text-slate-200">{{p}}</div>
          </div>
        ))}}
      </div>
    </Section>
  );
}}

/* ── CTA ── */
function CTA() {{
  const ref = useFadeIn();
  const subject = encodeURIComponent(`Re: ${{DATA.jobTitle}} at ${{DATA.company}}`);
  return (
    <div ref={{ref}} className="fade-in text-center py-10">
      <a href={{`mailto:${{DATA.email}}?subject=${{subject}}`}}
        className="inline-block bg-accent text-bg px-8 py-3 rounded-xl font-semibold text-lg hover:translate-y-[-2px] hover:shadow-lg hover:shadow-accent/20 transition-all">
        Let&apos;s Talk
      </a>
      <p className="mt-3 text-sm text-muted">{{DATA.email}}</p>
    </div>
  );
}}

/* ── App ── */
function App() {{
  return (
    <div className="max-w-[800px] mx-auto px-4 sm:px-6 relative z-10">
      <Hero />
      <AudioPlayer />
      <Bio />
      <FitScore />
      <Skills />
      <Metrics />
      <Projects />
      <CTA />
      <div className="text-center pb-6 text-xs text-muted">Built with ApplyPilot</div>
    </div>
  );
}}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
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


# ---------------------------------------------------------------------------
# GitHub Pages deployment
# ---------------------------------------------------------------------------

PAGES_REPO_NAME = "hire.nickjensen.co"
PAGES_REPO_PATH_DEFAULT = APP_DIR / PAGES_REPO_NAME


def deploy_to_github_pages(
    repo_path: Path | None = None,
    remote: str = "origin",
) -> int:
    """Deploy all generated landing pages to GitHub Pages.

    Copies landing page files into a local git repo and pushes.
    Creates the repo structure if it doesn't exist.

    Args:
        repo_path: Path to local clone. Defaults to ~/.applypilot/hire.nickjensen.co/.
        remote: Git remote name.

    Returns:
        Number of pages deployed.
    """
    import shutil
    import subprocess

    if repo_path is None:
        repo_path = PAGES_REPO_PATH_DEFAULT

    repo_path = Path(repo_path)
    repo_path.mkdir(parents=True, exist_ok=True)

    # Initialize git repo if needed
    git_dir = repo_path / ".git"
    if not git_dir.exists():
        subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
        log.info("Initialized git repo at %s", repo_path)

    # Ensure CNAME file
    cname_path = repo_path / "CNAME"
    if not cname_path.exists():
        cname_path.write_text("hire.nickjensen.co\n", encoding="utf-8")

    # Ensure .nojekyll (GitHub Pages serves files as-is)
    nojekyll = repo_path / ".nojekyll"
    if not nojekyll.exists():
        nojekyll.write_text("", encoding="utf-8")

    # Copy landing pages
    if not LANDING_DIR.exists():
        log.warning("No landing pages found at %s", LANDING_DIR)
        return 0

    deployed = 0
    for slug_dir in LANDING_DIR.iterdir():
        if not slug_dir.is_dir():
            continue
        index = slug_dir / "index.html"
        if not index.exists():
            continue

        dest = repo_path / slug_dir.name
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(index), str(dest / "index.html"))
        deployed += 1

    if deployed == 0:
        log.info("No landing pages to deploy.")
        return 0

    # Create a simple index page listing all deployed pages
    _write_index_page(repo_path)

    # Git add, commit, push
    subprocess.run(["git", "add", "-A"], cwd=repo_path, check=True, capture_output=True)

    # Check if there are changes to commit
    result = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=repo_path, capture_output=True,
    )
    if result.returncode == 0:
        log.info("No changes to deploy.")
        return deployed

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    subprocess.run(
        ["git", "commit", "-m", f"Deploy {deployed} landing pages ({now})"],
        cwd=repo_path, check=True, capture_output=True,
    )

    # Push (with retries)
    for attempt in range(4):
        push_result = subprocess.run(
            ["git", "push", "-u", remote, "main"],
            cwd=repo_path, capture_output=True, text=True,
        )
        if push_result.returncode == 0:
            log.info("Deployed %d pages to GitHub Pages.", deployed)
            return deployed
        log.warning("Push attempt %d failed: %s", attempt + 1, push_result.stderr.strip())
        if attempt < 3:
            import time as _time
            _time.sleep(2 ** (attempt + 1))

    log.error("Failed to push after 4 attempts.")
    return deployed


def _write_index_page(repo_path: Path) -> None:
    """Write a simple root index.html that lists all deployed pages."""
    pages = []
    for d in sorted(repo_path.iterdir()):
        if d.is_dir() and (d / "index.html").exists() and d.name != ".git":
            pages.append(d.name)

    items = "\n".join(
        f'        <a href="/{p}/" class="block px-4 py-3 rounded-lg bg-surface2 border border-border '
        f'hover:border-accent text-accent hover:text-white transition-all">{p}</a>'
        for p in pages
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>hire.nickjensen.co</title>
<script src="https://cdn.tailwindcss.com"></script>
<script>
tailwind.config = {{
  theme: {{ extend: {{ colors: {{
    bg: '#0a0f1a', surface: '#111827', surface2: '#1e293b',
    border: '#1e293b', accent: '#60a5fa', muted: '#64748b',
  }} }} }}
}}
</script>
</head>
<body class="bg-bg text-slate-200 font-sans">
<div class="max-w-lg mx-auto px-4 py-16">
  <h1 class="text-2xl font-bold mb-8 text-center">Applications</h1>
  <div class="flex flex-col gap-3">
{items}
  </div>
  <p class="text-center text-xs text-muted mt-10">Built with ApplyPilot</p>
</div>
</body>
</html>"""

    (repo_path / "index.html").write_text(html, encoding="utf-8")


# ---------------------------------------------------------------------------
# Batch runner
# ---------------------------------------------------------------------------

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
