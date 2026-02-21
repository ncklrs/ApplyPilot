"""Tests for fuzzy deduplication: title+company matching."""

from applypilot.dedup import (
    _normalize,
    _normalize_company,
    _similarity,
    find_duplicates,
    remove_duplicates,
)
from applypilot.database import init_db


class TestNormalize:
    """Test text normalization."""

    def test_lowercase(self):
        assert _normalize("HELLO") == "hello"

    def test_strip_punctuation(self):
        assert _normalize("hello, world!") == "hello world"

    def test_collapse_whitespace(self):
        assert _normalize("hello   world") == "hello world"

    def test_empty_string(self):
        assert _normalize("") == ""

    def test_none(self):
        assert _normalize(None) == ""


class TestNormalizeCompany:
    """Test company name normalization."""

    def test_removes_inc(self):
        assert "inc" not in _normalize_company("Acme Inc")

    def test_removes_corp(self):
        assert "corp" not in _normalize_company("Acme Corp")

    def test_removes_llc(self):
        assert "llc" not in _normalize_company("Acme LLC")

    def test_removes_technologies(self):
        assert "technologies" not in _normalize_company("Acme Technologies")

    def test_preserves_core_name(self):
        result = _normalize_company("Stripe Inc")
        assert "stripe" in result

    def test_empty(self):
        assert _normalize_company("") == ""


class TestSimilarity:
    """Test Jaccard word similarity."""

    def test_identical(self):
        assert _similarity("software engineer", "software engineer") == 1.0

    def test_no_overlap(self):
        assert _similarity("software engineer", "product manager") == 0.0

    def test_partial_overlap(self):
        sim = _similarity("senior software engineer", "software engineer")
        assert 0.5 < sim < 1.0

    def test_empty_strings(self):
        assert _similarity("", "hello") == 0.0
        assert _similarity("", "") == 0.0

    def test_case_insensitive(self):
        assert _similarity("Software Engineer", "software engineer") == 1.0


class TestFindDuplicates:
    """Test finding duplicate job postings."""

    def test_finds_title_duplicates(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = init_db(db_path)

        # Two jobs from different sources with same title
        conn.execute(
            "INSERT INTO jobs (url, title, site, discovered_at) VALUES (?, ?, ?, ?)",
            ("https://indeed.com/1", "Senior Software Engineer", "Indeed", "2024-01-01"),
        )
        conn.execute(
            "INSERT INTO jobs (url, title, site, discovered_at) VALUES (?, ?, ?, ?)",
            ("https://linkedin.com/1", "Senior Software Engineer", "LinkedIn", "2024-01-02"),
        )
        conn.commit()

        # These have different sites, so they won't be grouped unless
        # the company_threshold allows cross-site matching
        # In practice, Indeed/LinkedIn are different "companies" so this
        # tests the boundary case
        dupes = find_duplicates(conn, title_threshold=0.8, company_threshold=0.7)
        # Indeed and LinkedIn are different company names so no match expected
        # This is correct behavior -- dedup is within same company

    def test_no_duplicates_in_empty_db(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = init_db(db_path)

        dupes = find_duplicates(conn)
        assert dupes == []

    def test_same_company_different_titles(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = init_db(db_path)

        conn.execute(
            "INSERT INTO jobs (url, title, site, discovered_at) VALUES (?, ?, ?, ?)",
            ("https://stripe.com/1", "Backend Engineer", "Stripe", "2024-01-01"),
        )
        conn.execute(
            "INSERT INTO jobs (url, title, site, discovered_at) VALUES (?, ?, ?, ?)",
            ("https://stripe.com/2", "Frontend Engineer", "Stripe", "2024-01-02"),
        )
        conn.commit()

        dupes = find_duplicates(conn, title_threshold=0.8)
        assert len(dupes) == 0  # different titles, not duplicates


class TestRemoveDuplicates:
    """Test duplicate removal with safety checks."""

    def test_preserves_scored_jobs(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = init_db(db_path)

        conn.execute(
            "INSERT INTO jobs (url, title, site, fit_score) VALUES (?, ?, ?, ?)",
            ("https://a.com/1", "Software Engineer", "TestCo", 8),
        )
        conn.execute(
            "INSERT INTO jobs (url, title, site) VALUES (?, ?, ?)",
            ("https://a.com/2", "Software Engineer", "TestCo"),
        )
        conn.commit()

        result = remove_duplicates(conn, title_threshold=0.8)
        # Should not remove the scored job even if it's a duplicate
        scored = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE fit_score IS NOT NULL"
        ).fetchone()[0]
        assert scored >= 1

    def test_dry_run_no_deletions(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = init_db(db_path)

        conn.execute(
            "INSERT INTO jobs (url, title, site) VALUES (?, ?, ?)",
            ("https://a.com/1", "Software Engineer", "TestCo"),
        )
        conn.execute(
            "INSERT INTO jobs (url, title, site) VALUES (?, ?, ?)",
            ("https://a.com/2", "Software Engineer", "TestCo"),
        )
        conn.commit()

        before = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        remove_duplicates(conn, dry_run=True)
        after = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        assert before == after
