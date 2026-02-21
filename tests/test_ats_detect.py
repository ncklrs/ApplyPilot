"""Tests for ATS auto-detection module."""

import pytest
from applypilot.discovery.ats_detect import (
    _check_patterns,
    _build_result,
)


class TestCheckPatterns:
    """Test URL pattern matching for known ATS platforms."""

    def test_greenhouse_board_url(self):
        results = _check_patterns("https://boards.greenhouse.io/stripe/jobs/123")
        assert any(r["ats"] == "greenhouse" and r["token"] == "stripe" for r in results)

    def test_greenhouse_api_url(self):
        results = _check_patterns("https://boards-api.greenhouse.io/v1/boards/cloudflare/jobs")
        assert any(r["ats"] == "greenhouse" and r["token"] == "cloudflare" for r in results)

    def test_lever_url(self):
        results = _check_patterns("https://jobs.lever.co/netflix/abc123")
        assert any(r["ats"] == "lever" and r["token"] == "netflix" for r in results)

    def test_lever_api_url(self):
        results = _check_patterns("https://api.lever.co/v0/postings/shopify")
        assert any(r["ats"] == "lever" and r["token"] == "shopify" for r in results)

    def test_ashby_url(self):
        results = _check_patterns("https://jobs.ashbyhq.com/anthropic/some-job")
        assert any(r["ats"] == "ashby" and r["token"] == "anthropic" for r in results)

    def test_workday_url(self):
        results = _check_patterns("https://td.wd3.myworkdayjobs.com/TD_Bank_Careers")
        assert any(r["ats"] == "workday" and r["token"] == "td" for r in results)

    def test_workday_url_with_number(self):
        results = _check_patterns("https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite")
        assert any(
            r["ats"] == "workday" and r["token"] == "nvidia"
            for r in results
        )

    def test_no_match(self):
        results = _check_patterns("https://example.com/careers")
        assert results == []

    def test_multiple_matches(self):
        text = """
        Apply on Greenhouse: https://boards.greenhouse.io/acme
        Also on Lever: https://jobs.lever.co/acme
        """
        results = _check_patterns(text)
        ats_types = {r["ats"] for r in results}
        assert "greenhouse" in ats_types
        assert "lever" in ats_types

    def test_html_with_greenhouse_link(self):
        html = '<a href="https://boards.greenhouse.io/stripe/jobs/456">Apply</a>'
        results = _check_patterns(html)
        assert any(r["ats"] == "greenhouse" and r["token"] == "stripe" for r in results)


class TestBuildResult:
    """Test YAML snippet generation."""

    def test_greenhouse_result(self):
        result = _build_result("greenhouse", "stripe", "https://stripe.com/jobs")
        assert result["ats"] == "greenhouse"
        assert result["token"] == "stripe"
        assert 'board_token: "stripe"' in result["yaml_snippet"]
        assert result["config_file"] == "config/greenhouse.yaml"

    def test_lever_result(self):
        result = _build_result("lever", "netflix", "https://netflix.com/careers")
        assert 'company_slug: "netflix"' in result["yaml_snippet"]
        assert result["config_file"] == "config/lever.yaml"

    def test_ashby_result(self):
        result = _build_result("ashby", "anthropic", "https://anthropic.com/jobs")
        assert 'board_id: "anthropic"' in result["yaml_snippet"]
        assert result["config_file"] == "config/ashby.yaml"

    def test_workday_result(self):
        result = _build_result("workday", "td", "https://td.wd3.myworkdayjobs.com", extra={"wd_num": "3"})
        assert 'tenant: "td"' in result["yaml_snippet"]
        assert "wd3" in result["yaml_snippet"]
        assert result["config_file"] == "config/employers.yaml"

    def test_name_is_titlecased(self):
        result = _build_result("greenhouse", "my-company", "https://example.com")
        assert result["name"] == "My Company"

    def test_safe_key(self):
        result = _build_result("greenhouse", "my-company-name", "https://example.com")
        assert "my_company_name:" in result["yaml_snippet"]
