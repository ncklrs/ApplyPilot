"""Tests for shared discovery utilities: location filtering, HTML stripping, keyword matching."""

from applypilot.discovery.utils import location_ok, strip_html, matches_query


class TestLocationOk:
    """Test location accept/reject filtering."""

    def test_none_location_accepted(self):
        assert location_ok(None, [], []) is True

    def test_empty_location_accepted(self):
        assert location_ok("", [], []) is True

    def test_remote_always_accepted(self):
        assert location_ok("Remote", [], ["New York"]) is True
        assert location_ok("Work from home", [], ["New York"]) is True
        assert location_ok("Anywhere", [], ["New York"]) is True
        assert location_ok("San Francisco (WFH)", [], ["New York"]) is True

    def test_accept_pattern_matches(self):
        accept = ["San Francisco", "Toronto"]
        reject = []
        assert location_ok("San Francisco, CA", accept, reject) is True
        assert location_ok("Toronto, ON", accept, reject) is True

    def test_reject_pattern_blocks(self):
        accept = []
        reject = ["India", "Philippines"]
        assert location_ok("Bangalore, India", accept, reject) is False
        assert location_ok("Manila, Philippines", accept, reject) is False

    def test_reject_overrides_accept(self):
        accept = ["New York"]
        reject = ["New York"]
        assert location_ok("New York, NY", accept, reject) is False

    def test_case_insensitive(self):
        accept = ["toronto"]
        reject = []
        assert location_ok("TORONTO, ON", accept, reject) is True

    def test_unknown_location_rejected(self):
        accept = ["San Francisco"]
        reject = []
        assert location_ok("Unknown City", accept, reject) is False

    def test_distributed_accepted(self):
        assert location_ok("Distributed team", [], []) is True


class TestStripHtml:
    """Test HTML to plain text conversion."""

    def test_basic_html(self):
        html = "<p>Hello <b>world</b></p>"
        text = strip_html(html)
        assert "Hello" in text
        assert "world" in text
        assert "<p>" not in text
        assert "<b>" not in text

    def test_br_tags(self):
        html = "Line 1<br>Line 2<br/>Line 3"
        text = strip_html(html)
        assert "Line 1" in text
        assert "Line 2" in text

    def test_script_tags_removed(self):
        html = "<p>Visible</p><script>alert('xss')</script><p>Also visible</p>"
        text = strip_html(html)
        assert "Visible" in text
        assert "alert" not in text

    def test_style_tags_removed(self):
        html = "<style>.foo{color:red}</style><p>Content</p>"
        text = strip_html(html)
        assert "color" not in text
        assert "Content" in text

    def test_empty_string(self):
        assert strip_html("") == ""
        assert strip_html(None) == ""

    def test_nested_lists(self):
        html = "<ul><li>Item 1</li><li>Item 2</li></ul>"
        text = strip_html(html)
        assert "Item 1" in text
        assert "Item 2" in text

    def test_entities_preserved(self):
        html = "<p>A &amp; B</p>"
        text = strip_html(html)
        assert "A & B" in text


class TestMatchesQuery:
    """Test keyword-based query matching."""

    def test_exact_match(self):
        assert matches_query("Software Engineer", "software engineer") is True

    def test_partial_match(self):
        assert matches_query("Senior Software Engineer", "software engineer") is True

    def test_all_words_required(self):
        assert matches_query("Software Engineer", "software data") is False

    def test_case_insensitive(self):
        assert matches_query("SOFTWARE ENGINEER", "software engineer") is True

    def test_empty_query_matches_all(self):
        assert matches_query("Any Title", "") is True

    def test_empty_text_no_match(self):
        assert matches_query("", "engineer") is False

    def test_single_word_query(self):
        assert matches_query("Senior Data Engineer", "engineer") is True
        assert matches_query("Product Manager", "engineer") is False
