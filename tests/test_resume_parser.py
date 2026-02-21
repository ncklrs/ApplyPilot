"""Tests for resume format support: Markdown conversion and text cleanup."""

import pytest
from pathlib import Path

from applypilot.resume_parser import (
    convert_markdown_to_text,
    _clean_extracted_text,
    convert_resume,
)


class TestConvertMarkdownToText:
    """Test Markdown to plain text conversion."""

    def test_headers_uppercased(self, tmp_path):
        md = tmp_path / "resume.md"
        md.write_text("## Experience\n\nWorked at Acme Corp.\n")
        text = convert_markdown_to_text(md)
        assert "EXPERIENCE" in text
        assert "##" not in text

    def test_bold_stripped(self, tmp_path):
        md = tmp_path / "resume.md"
        md.write_text("**Senior Engineer** at Acme\n")
        text = convert_markdown_to_text(md)
        assert "Senior Engineer" in text
        assert "**" not in text

    def test_italic_stripped(self, tmp_path):
        md = tmp_path / "resume.md"
        md.write_text("*Python* and *JavaScript*\n")
        text = convert_markdown_to_text(md)
        assert "Python" in text
        assert "*" not in text

    def test_links_text_preserved(self, tmp_path):
        md = tmp_path / "resume.md"
        md.write_text("[GitHub](https://github.com/test)\n")
        text = convert_markdown_to_text(md)
        assert "GitHub" in text
        assert "https://" not in text

    def test_code_backticks_stripped(self, tmp_path):
        md = tmp_path / "resume.md"
        md.write_text("Experience with `Docker` and `Kubernetes`\n")
        text = convert_markdown_to_text(md)
        assert "Docker" in text
        assert "`" not in text

    def test_ordered_lists_normalized(self, tmp_path):
        md = tmp_path / "resume.md"
        md.write_text("1. First item\n2. Second item\n")
        text = convert_markdown_to_text(md)
        assert "- First item" in text
        assert "- Second item" in text

    def test_hr_removed(self, tmp_path):
        md = tmp_path / "resume.md"
        md.write_text("Above\n---\nBelow\n")
        text = convert_markdown_to_text(md)
        assert "Above" in text
        assert "Below" in text
        assert "---" not in text

    def test_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            convert_markdown_to_text(tmp_path / "nonexistent.md")


class TestCleanExtractedText:
    """Test text cleanup after extraction."""

    def test_normalizes_line_endings(self):
        text = "Line 1\r\nLine 2\rLine 3"
        result = _clean_extracted_text(text)
        assert "\r" not in result

    def test_collapses_blank_lines(self):
        text = "Line 1\n\n\n\n\nLine 2"
        result = _clean_extracted_text(text)
        assert "\n\n\n" not in result

    def test_strips_line_whitespace(self):
        text = "  Line 1  \n  Line 2  "
        result = _clean_extracted_text(text)
        assert result.startswith("Line 1")


class TestConvertResume:
    """Test auto-format detection and conversion."""

    def test_txt_passthrough(self, tmp_path):
        txt = tmp_path / "resume.txt"
        txt.write_text("Plain text resume content")
        text = convert_resume(txt)
        assert text == "Plain text resume content"

    def test_markdown_conversion(self, tmp_path):
        md = tmp_path / "resume.md"
        md.write_text("## Summary\n**Experienced** engineer.\n")
        text = convert_resume(md)
        assert "SUMMARY" in text
        assert "Experienced" in text

    def test_unsupported_format(self, tmp_path):
        bad = tmp_path / "resume.rtf"
        bad.write_text("RTF content")
        with pytest.raises(ValueError, match="Unsupported"):
            convert_resume(bad)

    def test_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            convert_resume(tmp_path / "nonexistent.pdf")

    def test_output_path(self, tmp_path):
        md = tmp_path / "resume.md"
        md.write_text("## Skills\nPython, JavaScript\n")
        out = tmp_path / "resume.txt"
        text = convert_resume(md, output_path=out)
        assert out.exists()
        assert out.read_text() == text
