"""Tests for the LLM client: provider detection, budget tracking, Anthropic adapter."""

import os
from unittest.mock import patch, MagicMock

import pytest

from applypilot import llm


class TestDetectProvider:
    """Test LLM provider auto-detection from environment variables."""

    def test_gemini_detected(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}, clear=False):
            with patch.object(llm, "_GEMINI_KEY", "test-key"):
                with patch.object(llm, "_OPENAI_KEY", ""):
                    with patch.object(llm, "_ANTHROPIC_KEY", ""):
                        with patch.object(llm, "_LOCAL_URL", ""):
                            url, model, key = llm._detect_provider()
                            assert "generativelanguage.googleapis.com" in url
                            assert model == "gemini-2.0-flash"
                            assert key == "test-key"

    def test_openai_detected(self):
        with patch.object(llm, "_GEMINI_KEY", ""):
            with patch.object(llm, "_OPENAI_KEY", "sk-test"):
                with patch.object(llm, "_ANTHROPIC_KEY", ""):
                    with patch.object(llm, "_LOCAL_URL", ""):
                        url, model, key = llm._detect_provider()
                        assert "api.openai.com" in url
                        assert model == "gpt-4o-mini"
                        assert key == "sk-test"

    def test_anthropic_detected(self):
        with patch.object(llm, "_GEMINI_KEY", ""):
            with patch.object(llm, "_OPENAI_KEY", ""):
                with patch.object(llm, "_ANTHROPIC_KEY", "sk-ant-test"):
                    with patch.object(llm, "_LOCAL_URL", ""):
                        url, model, key = llm._detect_provider()
                        assert "api.anthropic.com" in url
                        assert "claude" in model
                        assert key == "sk-ant-test"

    def test_local_llm_detected(self):
        with patch.object(llm, "_GEMINI_KEY", ""):
            with patch.object(llm, "_OPENAI_KEY", ""):
                with patch.object(llm, "_ANTHROPIC_KEY", ""):
                    with patch.object(llm, "_LOCAL_URL", "http://localhost:8080/v1"):
                        url, model, key = llm._detect_provider()
                        assert url == "http://localhost:8080/v1"
                        assert model == "local-model"

    def test_model_override(self):
        with patch.dict(os.environ, {"LLM_MODEL": "custom-model"}, clear=False):
            with patch.object(llm, "_GEMINI_KEY", "test"):
                with patch.object(llm, "_OPENAI_KEY", ""):
                    with patch.object(llm, "_ANTHROPIC_KEY", ""):
                        with patch.object(llm, "_LOCAL_URL", ""):
                            _, model, _ = llm._detect_provider()
                            assert model == "custom-model"

    def test_no_provider_raises(self):
        with patch.object(llm, "_GEMINI_KEY", ""):
            with patch.object(llm, "_OPENAI_KEY", ""):
                with patch.object(llm, "_ANTHROPIC_KEY", ""):
                    with patch.object(llm, "_LOCAL_URL", ""):
                        with pytest.raises(RuntimeError, match="No LLM provider configured"):
                            llm._detect_provider()

    def test_gemini_priority_over_openai(self):
        """Gemini should be selected when both Gemini and OpenAI keys exist."""
        with patch.object(llm, "_GEMINI_KEY", "gem-key"):
            with patch.object(llm, "_OPENAI_KEY", "oai-key"):
                with patch.object(llm, "_ANTHROPIC_KEY", ""):
                    with patch.object(llm, "_LOCAL_URL", ""):
                        url, _, _ = llm._detect_provider()
                        assert "generativelanguage" in url


class TestBudgetTracking:
    """Test LLM call budget enforcement."""

    def setup_method(self):
        """Reset budget state before each test."""
        llm._call_count = 0
        llm._max_calls = 0

    def test_unlimited_budget(self):
        llm.set_budget(0)
        for _ in range(100):
            llm._check_budget()  # should not raise

    def test_budget_enforced(self):
        llm.set_budget(3)
        llm._check_budget()  # 1
        llm._check_budget()  # 2
        llm._check_budget()  # 3
        with pytest.raises(RuntimeError, match="budget exhausted"):
            llm._check_budget()

    def test_get_call_count(self):
        llm._call_count = 0
        llm.set_budget(0)
        assert llm.get_call_count() == 0
        llm._check_budget()
        assert llm.get_call_count() == 1

    def teardown_method(self):
        """Reset budget state after each test."""
        llm._call_count = 0
        llm._max_calls = 0


class TestLLMClient:
    """Test LLMClient initialization and backend selection."""

    def test_anthropic_flag(self):
        client = llm.LLMClient("https://api.anthropic.com", "claude-sonnet-4-20250514", "key")
        assert client._is_anthropic is True

    def test_openai_flag(self):
        client = llm.LLMClient("https://api.openai.com/v1", "gpt-4o-mini", "key")
        assert client._is_anthropic is False

    def test_local_flag(self):
        client = llm.LLMClient("http://localhost:8080/v1", "local-model", "")
        assert client._is_anthropic is False

    def test_anthropic_client_setup(self):
        """Verify the Anthropic backend is correctly detected and configured."""
        client = llm.LLMClient("https://api.anthropic.com", "claude-test", "key")

        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello"},
        ]

        # We can't call _chat_anthropic directly without mocking HTTP,
        # but we can test that the client is set up to use it
        assert client._is_anthropic is True
        assert client.model == "claude-test"
