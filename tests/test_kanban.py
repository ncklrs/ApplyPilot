"""Tests for kanban dashboard view generation."""

import sqlite3
import pytest
from unittest.mock import patch

from applypilot.database import init_db, get_connection


@pytest.fixture
def db_with_kanban_jobs(tmp_path):
    """Create a temporary DB with test jobs at various stages."""
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)

    jobs = [
        # (url, title, site, score, full_desc, resume_path, cl_path, status, applied, user_stage)
        ("https://example.com/1", "Junior Dev", "Acme", 5, None, None, None, None, None, None),
        ("https://example.com/2", "Senior Dev", "BigCo", 8, "desc here", None, None, None, None, None),
        ("https://example.com/3", "Staff Eng", "MegaCorp", 9, "desc", "/tmp/r.txt", None, None, None, None),
        ("https://example.com/4", "Tech Lead", "StartupX", 7, "desc", "/tmp/r.txt", "/tmp/cl.txt", None, None, None),
        ("https://example.com/5", "SRE", "CloudCo", 8, "desc", "/tmp/r.txt", "/tmp/cl.txt", "applied", "2024-01-01", None),
        ("https://example.com/6", "VP Eng", "UniCorp", 9, "desc", "/tmp/r.txt", "/tmp/cl.txt", "applied", "2024-01-01", "interview"),
        ("https://example.com/7", "PM", "RejectCo", 7, "desc", "/tmp/r.txt", "/tmp/cl.txt", "applied", "2024-01-01", "rejected"),
    ]

    for url, title, site, score, desc, resume, cl, status, applied, user_stage in jobs:
        conn.execute(
            "INSERT INTO jobs (url, title, site, fit_score, full_description, "
            "tailored_resume_path, cover_letter_path, apply_status, applied_at, "
            "user_stage, discovered_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
            (url, title, site, score, desc, resume, cl, status, applied, user_stage),
        )
    conn.commit()

    return db_path, conn


def test_dashboard_generates_html_with_kanban(db_with_kanban_jobs, tmp_path):
    """Test that generate_dashboard produces valid HTML with kanban board."""
    db_path, conn = db_with_kanban_jobs

    with patch("applypilot.view.get_connection", return_value=conn), \
         patch("applypilot.view.APP_DIR", tmp_path):
        from applypilot.view import generate_dashboard

        out_path = tmp_path / "dashboard.html"
        result = generate_dashboard(str(out_path))
        assert result == str(out_path.resolve())

    html = out_path.read_text()

    # Verify grid view elements
    assert "ApplyPilot Dashboard" in html
    assert "Grid View" in html

    # Verify kanban board elements
    assert "Kanban Board" in html
    assert "kb-board" in html
    assert "kb-column" in html
    assert "switchTab" in html

    # Verify kanban columns exist
    assert "Discovered" in html
    assert "Scored" in html
    assert "Tailored" in html
    assert "Applied" in html
    assert "Interview" in html
    assert "Rejected" in html

    conn.close()


def test_dashboard_tab_switching_js(db_with_kanban_jobs, tmp_path):
    """Test that tab switching JavaScript is included."""
    db_path, conn = db_with_kanban_jobs

    with patch("applypilot.view.get_connection", return_value=conn), \
         patch("applypilot.view.APP_DIR", tmp_path):
        from applypilot.view import generate_dashboard

        out_path = tmp_path / "dashboard.html"
        generate_dashboard(str(out_path))

    html = out_path.read_text()
    assert "function switchTab" in html
    assert "tab-grid" in html
    assert "tab-kanban" in html

    conn.close()


def test_kanban_shows_job_titles(db_with_kanban_jobs, tmp_path):
    """Test that job titles appear in the kanban board."""
    db_path, conn = db_with_kanban_jobs

    with patch("applypilot.view.get_connection", return_value=conn), \
         patch("applypilot.view.APP_DIR", tmp_path):
        from applypilot.view import generate_dashboard

        out_path = tmp_path / "dashboard.html"
        generate_dashboard(str(out_path))

    html = out_path.read_text()

    # Jobs with score >= 5 should appear somewhere in the kanban
    assert "Senior Dev" in html
    assert "Staff Eng" in html
    assert "VP Eng" in html

    conn.close()
