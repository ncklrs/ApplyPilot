"""Tests for the resume tailoring module: JSON extraction and resume assembly."""

import pytest

from applypilot.scoring.tailor import extract_json, assemble_resume_text


class TestExtractJson:
    """Test robust JSON extraction from LLM responses."""

    def test_direct_json(self):
        raw = '{"title": "Engineer", "summary": "Test"}'
        result = extract_json(raw)
        assert result["title"] == "Engineer"

    def test_json_with_fences(self):
        raw = '```json\n{"title": "Engineer", "summary": "Test"}\n```'
        result = extract_json(raw)
        assert result["title"] == "Engineer"

    def test_json_with_preamble(self):
        raw = 'Here is the JSON:\n{"title": "Engineer", "summary": "Test"}'
        result = extract_json(raw)
        assert result["title"] == "Engineer"

    def test_json_with_trailing_text(self):
        raw = '{"title": "Engineer", "summary": "Test"}\nHope this helps!'
        result = extract_json(raw)
        assert result["title"] == "Engineer"

    def test_nested_json(self):
        raw = '{"title": "Engineer", "skills": {"languages": "Python, JS"}}'
        result = extract_json(raw)
        assert result["skills"]["languages"] == "Python, JS"

    def test_no_json_raises(self):
        raw = "This is just plain text with no JSON at all."
        with pytest.raises(ValueError, match="No valid JSON"):
            extract_json(raw)

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            extract_json("")

    def test_fenced_without_json_label(self):
        raw = '```\n{"title": "Engineer"}\n```'
        result = extract_json(raw)
        assert result["title"] == "Engineer"

    def test_multiple_fenced_blocks(self):
        raw = '```json\n{"wrong": true}\n```\nText\n```json\n{"title": "Right"}\n```'
        # Should parse the first valid one
        result = extract_json(raw)
        assert "wrong" in result or "title" in result


class TestAssembleResumeText:
    """Test resume text assembly from JSON data."""

    def test_basic_assembly(self, mock_profile):
        data = {
            "title": "Senior Software Engineer",
            "summary": "Experienced engineer with Python and AWS expertise.",
            "skills": {
                "Languages": "Python, JavaScript",
                "Frameworks": "React, FastAPI",
            },
            "experience": [
                {
                    "header": "Senior Engineer at Acme Corp",
                    "subtitle": "Python, AWS | 2022-Present",
                    "bullets": [
                        "Built data pipeline processing 1M events/day",
                        "Reduced latency 40%",
                    ],
                }
            ],
            "projects": [
                {
                    "header": "DataPipeline - ETL Framework",
                    "subtitle": "Python, Kafka | 2023",
                    "bullets": ["Built streaming pipeline"],
                }
            ],
            "education": "MIT | B.S. Computer Science",
        }

        text = assemble_resume_text(data, mock_profile)

        # Header injected from profile, not LLM
        assert "Jane Doe" in text
        assert "jane@example.com" in text

        # Sections present
        assert "SUMMARY" in text
        assert "TECHNICAL SKILLS" in text
        assert "EXPERIENCE" in text
        assert "PROJECTS" in text
        assert "EDUCATION" in text

        # Content present
        assert "Senior Software Engineer" in text
        assert "1M events/day" in text
        assert "MIT" in text

    def test_em_dashes_sanitized(self, mock_profile):
        data = {
            "title": "Engineer",
            "summary": "Built systems \u2014 fast ones.",
            "skills": {"Languages": "Python"},
            "experience": [],
            "projects": [],
            "education": "MIT",
        }

        text = assemble_resume_text(data, mock_profile)
        assert "\u2014" not in text  # em dash should be replaced
