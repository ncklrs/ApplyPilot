"""Tests for applypilot.landing — personalized landing page generator."""

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_profile():
    return {
        "personal": {
            "full_name": "Nick Jensen",
            "preferred_name": "Nick",
            "email": "nick@example.com",
            "linkedin_url": "https://linkedin.com/in/nick",
            "github_url": "https://github.com/nick",
            "portfolio_url": "",
            "website_url": "",
        },
        "experience": {
            "years_of_experience_total": "5",
            "education_level": "Bachelor's Degree",
            "target_role": "software engineer",
        },
        "resume_facts": {
            "preserved_companies": ["Acme Corp", "StartupCo"],
            "preserved_projects": ["DataPipe", "AutoScaler"],
            "preserved_school": "University of Example",
            "real_metrics": ["50% latency reduction", "10x throughput"],
        },
        "skills_boundary": {
            "languages": ["Python", "Go", "TypeScript"],
            "frameworks": ["FastAPI", "React", "Django"],
            "devops": ["Docker", "AWS", "Kubernetes"],
            "databases": ["PostgreSQL", "Redis"],
        },
    }


@pytest.fixture
def sample_job():
    return {
        "url": "https://example.com/jobs/123",
        "title": "Senior Backend Engineer",
        "site": "Stripe",
        "location": "Remote",
        "fit_score": 9,
        "score_reasoning": "Strong Python and API experience\nGood culture fit",
        "full_description": (
            "We are looking for a Senior Backend Engineer to build payment APIs "
            "using Python and Go. Experience with Docker, AWS, and PostgreSQL required. "
            "FastAPI experience preferred."
        ),
    }


# ---------------------------------------------------------------------------
# Tests: landing page generation
# ---------------------------------------------------------------------------

class TestGenerateLandingPage:
    def test_generates_html(self, sample_job, sample_profile, tmp_path):
        import applypilot.landing as landing_mod
        original_dir = landing_mod.LANDING_DIR
        landing_mod.LANDING_DIR = tmp_path / "landing"

        try:
            from applypilot.landing import generate_landing_page

            result = generate_landing_page(
                sample_job,
                pitch_script="Hey, I saw the backend role at Stripe. Love it.",
                audio_path=None,
                profile=sample_profile,
                base_url="https://hire.nickjensen.co",
            )

            assert result["slug"] == "stripe"
            assert result["url"] == "https://hire.nickjensen.co/stripe"
            assert result["path"].endswith("index.html")

            # Read and verify HTML content
            html = Path(result["path"]).read_text(encoding="utf-8")

            # Check key elements present in React data blob
            assert "Nick Jensen" in html
            assert "Stripe" in html
            assert "Senior Backend Engineer" in html
            assert "Hey, I saw the backend role" in html
            assert "linkedin.com/in/nick" in html
            assert "github.com/nick" in html
            assert "nick@example.com" in html
            assert "DataPipe" in html
            assert "50% latency reduction" in html
        finally:
            landing_mod.LANDING_DIR = original_dir

    def test_uses_react_and_tailwind(self, sample_job, sample_profile, tmp_path):
        import applypilot.landing as landing_mod
        original_dir = landing_mod.LANDING_DIR
        landing_mod.LANDING_DIR = tmp_path / "landing"

        try:
            from applypilot.landing import generate_landing_page

            result = generate_landing_page(
                sample_job, profile=sample_profile,
            )
            html = Path(result["path"]).read_text(encoding="utf-8")

            assert "cdn.tailwindcss.com" in html
            assert "react@18" in html
            assert "react-dom@18" in html
            assert "babel" in html
            assert "ReactDOM.createRoot" in html
        finally:
            landing_mod.LANDING_DIR = original_dir

    def test_skills_matching(self, sample_job, sample_profile, tmp_path):
        import applypilot.landing as landing_mod
        original_dir = landing_mod.LANDING_DIR
        landing_mod.LANDING_DIR = tmp_path / "landing"

        try:
            from applypilot.landing import _extract_skills_match

            skills = _extract_skills_match(sample_job, sample_profile)

            # Python, Go, Docker, AWS, PostgreSQL, FastAPI should match
            matched = [s for s in skills if s["matched"]]
            unmatched = [s for s in skills if not s["matched"]]

            matched_names = {s["skill"] for s in matched}
            assert "Python" in matched_names
            assert "Go" in matched_names
            assert "Docker" in matched_names
            assert "FastAPI" in matched_names
            assert "PostgreSQL" in matched_names

            # Redis, Django shouldn't match this JD
            unmatched_names = {s["skill"] for s in unmatched}
            assert "Django" in unmatched_names
        finally:
            landing_mod.LANDING_DIR = original_dir

    def test_slug_generation_company_only(self, sample_profile, tmp_path):
        """Slugs use company name only, not company + title."""
        from applypilot.landing import _make_slug

        assert _make_slug({"site": "Stripe"}) == "stripe"
        assert _make_slug({"site": "Stripe", "title": "Engineer"}) == "stripe"
        assert _make_slug({"site": "Open AI"}) == "open-ai"
        assert _make_slug({"site": "JPMorgan Chase"}) == "jpmorgan-chase"
        assert _make_slug({}) == "company"

    def test_no_pitch_script(self, sample_job, sample_profile, tmp_path):
        import applypilot.landing as landing_mod
        original_dir = landing_mod.LANDING_DIR
        landing_mod.LANDING_DIR = tmp_path / "landing"

        try:
            from applypilot.landing import generate_landing_page

            result = generate_landing_page(
                sample_job,
                pitch_script=None,
                audio_path=None,
                profile=sample_profile,
            )

            html = Path(result["path"]).read_text(encoding="utf-8")

            # Should still have the page, just pitchScript is null in data
            assert "Nick Jensen" in html
            assert "Stripe" in html
            assert '"pitchScript": null' in html or '"pitchScript":null' in html
        finally:
            landing_mod.LANDING_DIR = original_dir

    def test_photo_placeholder(self, sample_job, sample_profile, tmp_path):
        """When no photo exists, show initials placeholder."""
        import applypilot.landing as landing_mod
        original_dir = landing_mod.LANDING_DIR
        landing_mod.LANDING_DIR = tmp_path / "landing"

        try:
            from applypilot.landing import generate_landing_page

            result = generate_landing_page(
                sample_job,
                profile=sample_profile,
            )

            html = Path(result["path"]).read_text(encoding="utf-8")
            # photoUri should be null, initials should be NJ
            assert '"photoUri": null' in html or '"photoUri":null' in html
            assert '"initials": "NJ"' in html or '"initials":"NJ"' in html
        finally:
            landing_mod.LANDING_DIR = original_dir

    def test_photo_embedded(self, sample_job, sample_profile, tmp_path):
        """When a photo exists, embed it as base64."""
        import applypilot.landing as landing_mod
        original_dir = landing_mod.LANDING_DIR
        original_app_dir = landing_mod.APP_DIR
        landing_mod.LANDING_DIR = tmp_path / "landing"
        landing_mod.APP_DIR = tmp_path

        # Create a fake photo
        fake_photo = tmp_path / "photo.jpg"
        fake_photo.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)

        try:
            from applypilot.landing import generate_landing_page

            result = generate_landing_page(
                sample_job,
                profile=sample_profile,
            )

            html = Path(result["path"]).read_text(encoding="utf-8")
            assert "data:image/jpeg;base64," in html
        finally:
            landing_mod.LANDING_DIR = original_dir
            landing_mod.APP_DIR = original_app_dir

    def test_audio_embedded(self, sample_job, sample_profile, tmp_path):
        """When audio path provided, embed as base64."""
        import applypilot.landing as landing_mod
        original_dir = landing_mod.LANDING_DIR
        landing_mod.LANDING_DIR = tmp_path / "landing"

        # Create a fake mp3
        fake_audio = tmp_path / "pitch.mp3"
        fake_audio.write_bytes(b"\xff\xfb\x90\x00" * 50)

        try:
            from applypilot.landing import generate_landing_page

            result = generate_landing_page(
                sample_job,
                pitch_script="Test pitch.",
                audio_path=str(fake_audio),
                profile=sample_profile,
            )

            html = Path(result["path"]).read_text(encoding="utf-8")
            assert "data:audio/mpeg;base64," in html
            assert "waveform" in html
        finally:
            landing_mod.LANDING_DIR = original_dir

    def test_score_visualization(self, sample_job, sample_profile, tmp_path):
        import applypilot.landing as landing_mod
        original_dir = landing_mod.LANDING_DIR
        landing_mod.LANDING_DIR = tmp_path / "landing"

        try:
            from applypilot.landing import generate_landing_page

            result = generate_landing_page(
                sample_job,
                profile=sample_profile,
            )

            html = Path(result["path"]).read_text(encoding="utf-8")
            # Score data in JSON blob
            assert '"fitScore": 9' in html or '"fitScore":9' in html
            assert "Strong Match" in html
            assert "score-ring-fill" in html
        finally:
            landing_mod.LANDING_DIR = original_dir

    def test_custom_base_url(self, sample_job, sample_profile, tmp_path):
        import applypilot.landing as landing_mod
        original_dir = landing_mod.LANDING_DIR
        landing_mod.LANDING_DIR = tmp_path / "landing"

        try:
            from applypilot.landing import generate_landing_page

            result = generate_landing_page(
                sample_job,
                profile=sample_profile,
                base_url="https://custom.example.com",
            )

            assert result["url"] == "https://custom.example.com/stripe"
        finally:
            landing_mod.LANDING_DIR = original_dir


# ---------------------------------------------------------------------------
# Tests: batch run
# ---------------------------------------------------------------------------

class TestRunLandingPages:
    @patch("applypilot.voice.generate_pitch")
    @patch("applypilot.landing.generate_landing_page")
    @patch("applypilot.landing.load_profile")
    @patch("applypilot.landing.get_connection")
    def test_batch_generates_pages(self, mock_conn_fn, mock_profile, mock_gen_page, mock_gen_pitch):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE jobs (
                url TEXT PRIMARY KEY, title TEXT, site TEXT, location TEXT,
                fit_score INTEGER, full_description TEXT, score_reasoning TEXT,
                cover_letter_path TEXT, landing_page_path TEXT,
                landing_page_url TEXT, pitch_script TEXT, pitch_audio_path TEXT,
                landing_page_at TEXT
            )
        """)
        conn.execute(
            "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("https://example.com/1", "Engineer", "Stripe", "Remote",
             9, "Build APIs", "Good fit", "/path/to/cl.txt",
             None, None, None, None, None),
        )
        conn.commit()
        mock_conn_fn.return_value = conn

        mock_profile.return_value = {"personal": {"full_name": "Nick"}, "experience": {}, "resume_facts": {}, "skills_boundary": {}}
        mock_gen_pitch.return_value = {"script": "pitch", "audio_path": "/audio.mp3"}
        mock_gen_page.return_value = {"path": "/page.html", "slug": "stripe", "url": "https://hire.nickjensen.co/stripe"}

        from applypilot.landing import run_landing_pages
        result = run_landing_pages(min_score=7, limit=10)

        assert result["generated"] == 1
        assert result["errors"] == 0

        # Verify DB was updated
        row = conn.execute("SELECT landing_page_path, landing_page_url, pitch_script FROM jobs WHERE url = ?",
                           ("https://example.com/1",)).fetchone()
        assert row["landing_page_path"] == "/page.html"
        assert row["landing_page_url"] == "https://hire.nickjensen.co/stripe"
        assert row["pitch_script"] == "pitch"

    @patch("applypilot.landing.load_profile")
    @patch("applypilot.landing.get_connection")
    def test_no_eligible_jobs(self, mock_conn_fn, mock_profile):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE jobs (
                url TEXT PRIMARY KEY, title TEXT, site TEXT,
                fit_score INTEGER, full_description TEXT,
                cover_letter_path TEXT, landing_page_path TEXT
            )
        """)
        conn.commit()
        mock_conn_fn.return_value = conn
        mock_profile.return_value = {"personal": {}, "experience": {}, "resume_facts": {}, "skills_boundary": {}}

        from applypilot.landing import run_landing_pages
        result = run_landing_pages(min_score=7)

        assert result["generated"] == 0
        assert result["errors"] == 0


# ---------------------------------------------------------------------------
# Tests: deploy
# ---------------------------------------------------------------------------

class TestDeploy:
    def test_copies_pages_to_repo(self, tmp_path):
        import applypilot.landing as landing_mod
        original_dir = landing_mod.LANDING_DIR
        landing_dir = tmp_path / "landing_pages"
        landing_mod.LANDING_DIR = landing_dir

        # Create a landing page
        page_dir = landing_dir / "stripe"
        page_dir.mkdir(parents=True)
        (page_dir / "index.html").write_text("<html>stripe</html>")

        repo_path = tmp_path / "repo"

        try:
            from applypilot.landing import deploy_to_github_pages

            with patch("subprocess.run") as mock_run:
                # Mock git init, add, diff (has changes), commit, push
                mock_run.return_value = MagicMock(returncode=0)
                # diff --cached --quiet returns 1 = has changes
                def side_effect(cmd, **kwargs):
                    result = MagicMock(returncode=0, stdout="", stderr="")
                    if "diff" in cmd and "--cached" in cmd:
                        result.returncode = 1  # has changes
                    return result
                mock_run.side_effect = side_effect

                count = deploy_to_github_pages(repo_path=repo_path)

            assert count == 1
            assert (repo_path / "stripe" / "index.html").exists()
            assert (repo_path / "CNAME").read_text().strip() == "hire.nickjensen.co"
            assert (repo_path / ".nojekyll").exists()
            assert (repo_path / "index.html").exists()  # Root index page
        finally:
            landing_mod.LANDING_DIR = original_dir

    def test_no_pages_returns_zero(self, tmp_path):
        import applypilot.landing as landing_mod
        original_dir = landing_mod.LANDING_DIR
        landing_mod.LANDING_DIR = tmp_path / "empty_landing"

        try:
            from applypilot.landing import deploy_to_github_pages
            count = deploy_to_github_pages(repo_path=tmp_path / "repo")
            assert count == 0
        finally:
            landing_mod.LANDING_DIR = original_dir


# ---------------------------------------------------------------------------
# Tests: HTML structure
# ---------------------------------------------------------------------------

class TestHTMLStructure:
    def test_responsive_meta(self, sample_job, sample_profile, tmp_path):
        import applypilot.landing as landing_mod
        original_dir = landing_mod.LANDING_DIR
        landing_mod.LANDING_DIR = tmp_path / "landing"

        try:
            from applypilot.landing import generate_landing_page

            result = generate_landing_page(sample_job, profile=sample_profile)
            html = Path(result["path"]).read_text(encoding="utf-8")

            assert 'viewport' in html
            assert 'width=device-width' in html
            assert '<!DOCTYPE html>' in html
            assert 'fade-in' in html
            assert 'IntersectionObserver' in html
        finally:
            landing_mod.LANDING_DIR = original_dir

    def test_xss_escaping(self, sample_profile, tmp_path):
        """Ensure user-controlled strings are HTML-escaped."""
        import applypilot.landing as landing_mod
        original_dir = landing_mod.LANDING_DIR
        landing_mod.LANDING_DIR = tmp_path / "landing"

        malicious_job = {
            "url": "https://example.com/1",
            "title": '<script>alert("xss")</script>',
            "site": "Evil<Corp>",
            "location": "Nowhere",
            "fit_score": 8,
            "full_description": "Normal description",
            "score_reasoning": "",
        }

        try:
            from applypilot.landing import generate_landing_page

            result = generate_landing_page(
                malicious_job,
                pitch_script='<script>alert("xss")</script>',
                profile=sample_profile,
            )

            html = Path(result["path"]).read_text(encoding="utf-8")

            # The title/meta tags should have escaped HTML
            assert '&lt;script&gt;' in html or '\\u003c' in html
            # The JSON data blob escapes via json.dumps ensure_ascii
            # Raw unescaped script tags from user data should not appear
            assert '<script>alert("xss")</script>' not in html
        finally:
            landing_mod.LANDING_DIR = original_dir
