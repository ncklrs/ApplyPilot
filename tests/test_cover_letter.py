"""Tests for cover letter validation: banned words, length, structure."""

from applypilot.scoring.validator import validate_cover_letter, sanitize_text


class TestValidateCoverLetter:
    """Test programmatic cover letter validation."""

    def test_valid_cover_letter(self):
        letter = (
            "Dear Hiring Manager,\n\n"
            "At Acme Corp, I built a data pipeline processing 1M events/day using "
            "Python and Kafka. The same architecture would solve the throughput "
            "challenge your team faces with real-time analytics.\n\n"
            "Two results from my recent work: reduced API latency 40% by redesigning "
            "the caching layer with Redis, and automated deployment pipelines cutting "
            "release cycles from 2 hours to 15 minutes using Docker and Terraform.\n\n"
            "Your team's focus on distributed systems at scale is compelling. "
            "Happy to walk through any of this in more detail.\n\n"
            "Jane"
        )
        result = validate_cover_letter(letter)
        assert result["passed"] is True
        assert len(result["errors"]) == 0

    def test_banned_word_detected(self):
        letter = (
            "Dear Hiring Manager,\n\n"
            "I am passionate about this role and eager to apply my skills.\n\n"
            "Jane"
        )
        result = validate_cover_letter(letter)
        assert result["passed"] is False
        assert any("Banned words" in e for e in result["errors"])

    def test_too_long(self):
        letter = "Dear Hiring Manager,\n\n" + "word " * 310 + "\n\nJane"
        result = validate_cover_letter(letter)
        assert result["passed"] is False
        assert any("Too long" in e for e in result["errors"])

    def test_em_dash_detected(self):
        letter = "Dear Hiring Manager,\n\nI built systems \u2014 really good ones.\n\nJane"
        result = validate_cover_letter(letter)
        assert result["passed"] is False
        assert any("em dash" in e.lower() for e in result["errors"])

    def test_must_start_with_dear(self):
        letter = "Hi there,\n\nI am interested in this role.\n\nJane"
        result = validate_cover_letter(letter)
        assert result["passed"] is False
        assert any("Dear" in e for e in result["errors"])

    def test_llm_self_talk_detected(self):
        letter = (
            "Dear Hiring Manager,\n\n"
            "I am sorry, let me try again with a better version.\n\n"
            "Jane"
        )
        result = validate_cover_letter(letter)
        assert result["passed"] is False
        assert any("self-talk" in e.lower() for e in result["errors"])


class TestSanitizeText:
    """Test automatic text sanitization."""

    def test_em_dash_replaced(self):
        assert "\u2014" not in sanitize_text("hello \u2014 world")

    def test_en_dash_replaced(self):
        assert "\u2013" not in sanitize_text("2020\u20132023")

    def test_smart_quotes_replaced(self):
        text = sanitize_text("\u201cHello\u201d and \u2018world\u2019")
        assert "\u201c" not in text
        assert "\u201d" not in text
        assert "\u2018" not in text
        assert "\u2019" not in text

    def test_strips_whitespace(self):
        assert sanitize_text("  hello  ") == "hello"
