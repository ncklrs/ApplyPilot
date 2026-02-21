"""Tests for Greenhouse ATS scraper: API parsing, filtering, storage."""

import json
import sqlite3
from unittest.mock import patch, MagicMock

import pytest

from applypilot.discovery.greenhouse import (
    search_employer,
    store_results,
    load_employers,
)
from applypilot.database import init_db


@pytest.fixture
def sample_greenhouse_jobs():
    """Sample Greenhouse API response jobs."""
    return [
        {
            "id": 1001,
            "title": "Senior Software Engineer",
            "location": {"name": "San Francisco, CA"},
            "absolute_url": "https://boards.greenhouse.io/testco/jobs/1001",
            "content": "<p>We are looking for a senior engineer with Python experience.</p>",
            "updated_at": "2024-01-15T10:00:00Z",
            "departments": [{"name": "Engineering"}],
        },
        {
            "id": 1002,
            "title": "Product Manager",
            "location": {"name": "New York, NY"},
            "absolute_url": "https://boards.greenhouse.io/testco/jobs/1002",
            "content": "<p>Lead product strategy for our platform.</p>",
            "updated_at": "2024-01-14T10:00:00Z",
            "departments": [{"name": "Product"}],
        },
        {
            "id": 1003,
            "title": "Data Engineer",
            "location": {"name": "Remote"},
            "absolute_url": "https://boards.greenhouse.io/testco/jobs/1003",
            "content": "<p>Build data pipelines at scale.</p>",
            "updated_at": "2024-01-13T10:00:00Z",
            "departments": [{"name": "Data"}],
        },
    ]


class TestSearchEmployer:
    """Test Greenhouse employer search with filtering."""

    def test_filters_by_query(self, sample_greenhouse_jobs):
        employer = {"name": "TestCo", "board_token": "testco"}

        with patch("applypilot.discovery.greenhouse.greenhouse_list_jobs", return_value=sample_greenhouse_jobs):
            results = search_employer(
                "testco", employer, "software engineer",
                accept_locs=["San Francisco", "New York", "Remote"],
            )

        assert len(results) == 1
        assert results[0]["title"] == "Senior Software Engineer"

    def test_filters_by_location(self, sample_greenhouse_jobs):
        employer = {"name": "TestCo", "board_token": "testco"}

        with patch("applypilot.discovery.greenhouse.greenhouse_list_jobs", return_value=sample_greenhouse_jobs):
            results = search_employer(
                "testco", employer, "engineer",
                accept_locs=["San Francisco"],
                reject_locs=[],
            )

        titles = [r["title"] for r in results]
        assert "Senior Software Engineer" in titles
        # Data Engineer is Remote, should also be accepted
        assert "Data Engineer" in titles

    def test_strips_html_from_description(self, sample_greenhouse_jobs):
        employer = {"name": "TestCo", "board_token": "testco"}

        with patch("applypilot.discovery.greenhouse.greenhouse_list_jobs", return_value=sample_greenhouse_jobs):
            results = search_employer(
                "testco", employer, "software",
                accept_locs=["San Francisco"],
            )

        assert len(results) == 1
        # HTML tags should be stripped
        assert "<p>" not in results[0]["description"]
        assert "Python experience" in results[0]["description"]

    def test_handles_api_error(self):
        employer = {"name": "TestCo", "board_token": "testco"}

        with patch("applypilot.discovery.greenhouse.greenhouse_list_jobs", side_effect=Exception("API down")):
            results = search_employer("testco", employer, "engineer")

        assert results == []

    def test_empty_board_returns_empty(self):
        employer = {"name": "TestCo", "board_token": "testco"}

        with patch("applypilot.discovery.greenhouse.greenhouse_list_jobs", return_value=[]):
            results = search_employer("testco", employer, "engineer")

        assert results == []


class TestStoreResults:
    """Test Greenhouse job storage in database."""

    def test_stores_pre_enriched(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = init_db(db_path)

        jobs = [
            {
                "title": "Engineer",
                "location": "Remote",
                "description": "A" * 300,  # long enough for full_description
                "apply_url": "https://boards.greenhouse.io/test/jobs/1",
                "employer_name": "TestCo",
            },
        ]

        new, existing = store_results(conn, jobs)
        assert new == 1
        assert existing == 0

        row = conn.execute("SELECT * FROM jobs WHERE url = ?",
                           ("https://boards.greenhouse.io/test/jobs/1",)).fetchone()
        assert row is not None
        # Should be pre-enriched
        assert row["full_description"] is not None
        assert row["detail_scraped_at"] is not None
        assert row["strategy"] == "greenhouse_api"

    def test_deduplicates(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = init_db(db_path)

        jobs = [
            {"title": "Engineer", "location": "Remote",
             "description": "A" * 300,
             "apply_url": "https://boards.greenhouse.io/test/jobs/1",
             "employer_name": "TestCo"},
        ]

        store_results(conn, jobs)
        new, existing = store_results(conn, jobs)
        assert new == 0
        assert existing == 1
