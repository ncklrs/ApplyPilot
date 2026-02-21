"""Tests for the job scoring module: response parsing and validation."""

from applypilot.scoring.scorer import _parse_score_response


class TestParseScoreResponse:
    """Test parsing of LLM score response format."""

    def test_standard_format(self):
        response = (
            "SCORE: 8\n"
            "KEYWORDS: Python, AWS, Docker, Kubernetes\n"
            "REASONING: Strong match with required Python and cloud experience."
        )
        result = _parse_score_response(response)
        assert result["score"] == 8
        assert "Python" in result["keywords"]
        assert "Strong match" in result["reasoning"]

    def test_score_clamped_to_10(self):
        response = "SCORE: 15\nKEYWORDS: none\nREASONING: test"
        result = _parse_score_response(response)
        assert result["score"] == 10

    def test_score_clamped_to_1(self):
        response = "SCORE: 0\nKEYWORDS: none\nREASONING: test"
        result = _parse_score_response(response)
        assert result["score"] == 1

    def test_score_with_extra_text(self):
        response = "SCORE: 7/10\nKEYWORDS: React, Node\nREASONING: Good fit."
        result = _parse_score_response(response)
        assert result["score"] == 7

    def test_missing_score(self):
        response = "KEYWORDS: Python\nREASONING: No score provided."
        result = _parse_score_response(response)
        assert result["score"] == 0

    def test_malformed_response(self):
        response = "This is a completely unstructured response without any fields."
        result = _parse_score_response(response)
        assert result["score"] == 0
        assert result["reasoning"] == response

    def test_empty_response(self):
        result = _parse_score_response("")
        assert result["score"] == 0

    def test_extra_whitespace(self):
        response = "  SCORE:   9  \n  KEYWORDS:  SQL, Python  \n  REASONING:  Great fit  "
        result = _parse_score_response(response)
        assert result["score"] == 9
        assert "SQL" in result["keywords"]
