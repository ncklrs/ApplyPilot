"""Tests for detail enrichment module: JSON-LD extraction."""

import pytest

from applypilot.enrichment.detail import extract_from_json_ld


class TestExtractFromJsonLd:
    """Test JSON-LD job posting extraction.

    extract_from_json_ld expects an intel dict with a 'json_ld' key
    containing parsed JSON-LD objects, not raw HTML.
    """

    def test_standard_job_posting(self):
        intel = {
            "json_ld": [
                {
                    "@type": "JobPosting",
                    "title": "Software Engineer",
                    "description": "Build amazing software. " * 20,  # needs >50 chars
                    "datePosted": "2024-01-01",
                    "hiringOrganization": {
                        "name": "Acme Corp"
                    },
                    "jobLocation": {
                        "@type": "Place",
                        "address": {
                            "addressLocality": "San Francisco",
                            "addressRegion": "CA"
                        }
                    },
                }
            ]
        }
        result = extract_from_json_ld(intel)
        assert result is not None
        assert result.get("full_description") is not None

    def test_no_json_ld(self):
        intel = {"json_ld": []}
        result = extract_from_json_ld(intel)
        assert result is None

    def test_non_job_posting_ld(self):
        intel = {
            "json_ld": [
                {
                    "@type": "Organization",
                    "name": "Acme Corp"
                }
            ]
        }
        result = extract_from_json_ld(intel)
        assert result is None

    def test_empty_intel(self):
        intel = {}
        result = extract_from_json_ld(intel)
        assert result is None
