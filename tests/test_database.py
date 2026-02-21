"""Tests for database layer: schema, storage, stats, and queries."""

import sqlite3

import pytest

from applypilot.database import (
    init_db,
    get_connection,
    get_stats,
    store_jobs,
    get_jobs_by_stage,
    ensure_columns,
)


class TestInitDb:
    """Test database schema creation."""

    def test_creates_table(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = init_db(db_path)
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = [t[0] for t in tables]
        assert "jobs" in table_names

    def test_idempotent(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn1 = init_db(db_path)
        conn1.execute(
            "INSERT INTO jobs (url, title) VALUES (?, ?)",
            ("https://test.com/1", "Test Job"),
        )
        conn1.commit()

        # Re-init should not destroy data
        conn2 = init_db(db_path)
        count = conn2.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        assert count == 1

    def test_all_columns_exist(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = init_db(db_path)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}

        expected = {
            "url", "title", "salary", "description", "location", "site",
            "strategy", "discovered_at", "full_description", "application_url",
            "detail_scraped_at", "detail_error", "fit_score", "score_reasoning",
            "scored_at", "tailored_resume_path", "tailored_at", "tailor_attempts",
            "cover_letter_path", "cover_letter_at", "cover_attempts",
            "applied_at", "apply_status", "apply_error", "apply_attempts",
            "agent_id", "last_attempted_at", "apply_duration_ms",
            "apply_task_id", "verification_confidence",
        }
        assert expected.issubset(columns)


class TestStoreJobs:
    """Test job storage and deduplication."""

    def test_stores_new_jobs(self, tmp_db):
        jobs = [
            {"url": "https://test.com/1", "title": "Engineer", "salary": "100K",
             "description": "Build things", "location": "Remote"},
            {"url": "https://test.com/2", "title": "Manager", "salary": "120K",
             "description": "Manage things", "location": "NYC"},
        ]
        new, existing = store_jobs(tmp_db, jobs, "TestSite", "manual")
        assert new == 2
        assert existing == 0

    def test_deduplicates_by_url(self, tmp_db):
        jobs = [{"url": "https://test.com/1", "title": "Engineer"}]
        store_jobs(tmp_db, jobs, "TestSite", "manual")

        # Try storing same URL again
        new, existing = store_jobs(tmp_db, jobs, "TestSite", "manual")
        assert new == 0
        assert existing == 1

    def test_skips_jobs_without_url(self, tmp_db):
        jobs = [{"title": "No URL Job"}]  # missing url
        new, existing = store_jobs(tmp_db, jobs, "TestSite", "manual")
        assert new == 0

    def test_stores_site_and_strategy(self, tmp_db):
        jobs = [{"url": "https://test.com/1", "title": "Engineer"}]
        store_jobs(tmp_db, jobs, "Greenhouse", "greenhouse_api")

        row = tmp_db.execute("SELECT site, strategy FROM jobs WHERE url = ?",
                             ("https://test.com/1",)).fetchone()
        assert row[0] == "Greenhouse"
        assert row[1] == "greenhouse_api"


class TestGetStats:
    """Test pipeline statistics queries."""

    def test_empty_db(self, tmp_db):
        stats = get_stats(tmp_db)
        assert stats["total"] == 0
        assert stats["scored"] == 0
        assert stats["applied"] == 0

    def test_counts_correct(self, tmp_db):
        # Insert jobs at various stages
        tmp_db.execute(
            "INSERT INTO jobs (url, title, site) VALUES (?, ?, ?)",
            ("https://a.com", "Job A", "SiteA"),
        )
        tmp_db.execute(
            "INSERT INTO jobs (url, title, site, full_description, fit_score) VALUES (?, ?, ?, ?, ?)",
            ("https://b.com", "Job B", "SiteB", "Full desc here", 8),
        )
        tmp_db.execute(
            "INSERT INTO jobs (url, title, site, full_description, fit_score, tailored_resume_path, applied_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("https://c.com", "Job C", "SiteC", "Full desc", 9, "/path/resume.txt", "2024-01-01"),
        )
        tmp_db.commit()

        stats = get_stats(tmp_db)
        assert stats["total"] == 3
        assert stats["with_description"] == 2
        assert stats["scored"] == 2
        assert stats["tailored"] == 1
        assert stats["applied"] == 1


class TestGetJobsByStage:
    """Test filtered job queries by pipeline stage."""

    def test_pending_detail(self, tmp_db):
        tmp_db.execute("INSERT INTO jobs (url, title) VALUES (?, ?)", ("https://a.com", "Job A"))
        tmp_db.execute("INSERT INTO jobs (url, title, detail_scraped_at) VALUES (?, ?, ?)",
                       ("https://b.com", "Job B", "2024-01-01"))
        tmp_db.commit()

        jobs = get_jobs_by_stage(tmp_db, "pending_detail")
        assert len(jobs) == 1
        assert jobs[0]["url"] == "https://a.com"

    def test_pending_score(self, tmp_db):
        tmp_db.execute("INSERT INTO jobs (url, title, full_description) VALUES (?, ?, ?)",
                       ("https://a.com", "Job A", "Description here"))
        tmp_db.execute("INSERT INTO jobs (url, title, full_description, fit_score) VALUES (?, ?, ?, ?)",
                       ("https://b.com", "Job B", "Description", 7))
        tmp_db.commit()

        jobs = get_jobs_by_stage(tmp_db, "pending_score")
        assert len(jobs) == 1
        assert jobs[0]["url"] == "https://a.com"


class TestEnsureColumns:
    """Test forward migration for new columns."""

    def test_no_changes_needed(self, tmp_db):
        added = ensure_columns(tmp_db)
        assert added == []  # all columns already exist

    def test_adds_missing_column(self, tmp_path):
        db_path = tmp_path / "old.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE jobs (url TEXT PRIMARY KEY, title TEXT)")
        conn.commit()

        from applypilot.database import _local
        if not hasattr(_local, 'connections'):
            _local.connections = {}
        _local.connections[str(db_path)] = conn

        added = ensure_columns(conn)
        assert len(added) > 0
        assert "fit_score" in added
