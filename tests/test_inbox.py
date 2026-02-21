"""Tests for email inbox monitor."""

import pytest
from applypilot.inbox import classify_email, _decode_subject, _extract_sender_domain


class TestClassifyEmail:
    """Test email classification logic."""

    def test_confirmation(self):
        assert classify_email("Application Received - Software Engineer", "") == "confirmation"
        assert classify_email("Thank you for applying", "") == "confirmation"
        assert classify_email("We received your application", "") == "confirmation"
        assert classify_email("Application submitted successfully", "") == "confirmation"

    def test_interview(self):
        assert classify_email("Interview scheduled for Monday", "") == "interview"
        assert classify_email("Invitation to interview", "") == "interview"
        assert classify_email("Phone screen - next steps", "") == "interview"
        assert classify_email("", "We'd like to speak with you about the role") == "interview"
        assert classify_email("", "We were impressed by your background") == "interview"

    def test_rejection(self):
        assert classify_email("We will not be moving forward", "") == "rejection"
        assert classify_email("Position has been filled", "") == "rejection"
        assert classify_email("Unfortunately, not selected", "") == "rejection"
        assert classify_email("", "We decided not to proceed with your candidacy") == "rejection"

    def test_follow_up(self):
        assert classify_email("Follow up on your application", "") == "follow_up"
        assert classify_email("Checking in on your application", "") == "follow_up"

    def test_unrelated(self):
        assert classify_email("Weekly newsletter", "Buy our product today!") is None
        assert classify_email("Your order has shipped", "Package tracking") is None

    def test_interview_takes_priority_over_confirmation(self):
        # If both patterns match, interview should win
        assert classify_email(
            "Application received - interview scheduled",
            ""
        ) == "interview"

    def test_body_matters(self):
        # Subject is neutral but body contains the signal
        assert classify_email(
            "Update from Acme Corp",
            "We regret to inform you that we will not be moving forward."
        ) == "rejection"

    def test_empty_inputs(self):
        assert classify_email("", "") is None


class TestExtractSenderDomain:
    """Test email sender domain extraction."""

    def test_simple_address(self):
        from email.message import EmailMessage
        msg = EmailMessage()
        msg["From"] = "jobs@stripe.com"
        assert _extract_sender_domain(msg) == "stripe.com"

    def test_formatted_address(self):
        from email.message import EmailMessage
        msg = EmailMessage()
        msg["From"] = "Stripe Recruiting <noreply@stripe.com>"
        assert _extract_sender_domain(msg) == "stripe.com"

    def test_no_from(self):
        from email.message import EmailMessage
        msg = EmailMessage()
        assert _extract_sender_domain(msg) == ""
