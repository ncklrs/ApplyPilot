"""Tests for applypilot.voice — ElevenLabs TTS integration."""

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_profile():
    return {
        "personal": {
            "full_name": "Nick Jensen",
            "preferred_name": "Nick",
            "email": "nick@example.com",
            "linkedin_url": "https://linkedin.com/in/nick",
            "github_url": "https://github.com/nick",
        },
        "experience": {
            "years_of_experience_total": "5",
            "target_role": "software engineer",
        },
        "resume_facts": {
            "preserved_companies": ["Acme Corp", "StartupCo"],
            "preserved_projects": ["DataPipe", "AutoScaler"],
            "preserved_school": "University of Example",
            "real_metrics": ["50% latency reduction", "10x throughput"],
        },
        "skills_boundary": {
            "languages": ["Python", "Go"],
            "frameworks": ["FastAPI", "React"],
        },
    }


@pytest.fixture
def sample_job():
    return {
        "url": "https://example.com/jobs/123",
        "title": "Senior Backend Engineer",
        "site": "Stripe",
        "location": "Remote",
        "fit_score": 9,
        "score_reasoning": "Strong Python and API experience\nGood culture fit",
        "full_description": "We are looking for a Senior Backend Engineer to build payment APIs using Python and Go.",
    }


# ---------------------------------------------------------------------------
# Tests: pitch script generation
# ---------------------------------------------------------------------------

class TestGeneratePitchScript:
    @patch("applypilot.voice.get_client")
    def test_generates_script(self, mock_get_client, sample_job, sample_profile):
        mock_client = MagicMock()
        mock_client.chat.return_value = "Hey, I saw you're building payment APIs at Stripe. At Acme Corp, I built DataPipe which cut latency 50%. Would love to chat."
        mock_get_client.return_value = mock_client

        from applypilot.voice import generate_pitch_script
        script = generate_pitch_script(sample_job, sample_profile)

        assert "Stripe" in script or "payment" in script
        mock_client.chat.assert_called_once()
        # Check system prompt includes profile data
        call_args = mock_client.chat.call_args
        messages = call_args[0][0]
        assert "Nick" in messages[0]["content"]
        assert "Senior Backend Engineer" in messages[1]["content"]

    @patch("applypilot.voice.get_client")
    def test_handles_minimal_profile(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.chat.return_value = "Hey, I'd love to chat about this role."
        mock_get_client.return_value = mock_client

        from applypilot.voice import generate_pitch_script

        minimal_profile = {"personal": {}, "experience": {}, "resume_facts": {}, "skills_boundary": {}}
        minimal_job = {"title": "Engineer", "site": "Co", "full_description": "Build things."}
        script = generate_pitch_script(minimal_job, minimal_profile)

        assert isinstance(script, str)
        assert len(script) > 0


# ---------------------------------------------------------------------------
# Tests: audio synthesis
# ---------------------------------------------------------------------------

class TestSynthesizeAudio:
    @patch("applypilot.voice._get_elevenlabs_config", return_value=("test-key", "voice-123"))
    @patch("applypilot.voice.httpx.post")
    def test_synthesis_success(self, mock_post, mock_config, tmp_path):
        mock_response = MagicMock()
        mock_response.content = b"\xff\xfb\x90\x00" * 100  # fake mp3 bytes
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        from applypilot.voice import synthesize_audio

        output = tmp_path / "test_pitch.mp3"
        result = synthesize_audio("Hello, this is a test.", output)

        assert result == output
        assert output.exists()
        assert output.read_bytes() == mock_response.content
        mock_post.assert_called_once()

        # Verify API call
        call_kwargs = mock_post.call_args
        assert "voice-123" in call_kwargs[0][0]  # URL contains voice ID
        assert call_kwargs[1]["headers"]["xi-api-key"] == "test-key"

    @patch("applypilot.voice._get_elevenlabs_config", return_value=("test-key", "voice-123"))
    @patch("applypilot.voice.httpx.post")
    def test_synthesis_http_error(self, mock_post, mock_config, tmp_path):
        import httpx
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "401 Unauthorized", request=MagicMock(), response=MagicMock()
        )
        mock_post.return_value = mock_response

        from applypilot.voice import synthesize_audio

        output = tmp_path / "test.mp3"
        with pytest.raises(httpx.HTTPStatusError):
            synthesize_audio("Hello", output)


# ---------------------------------------------------------------------------
# Tests: config loading
# ---------------------------------------------------------------------------

class TestElevenLabsConfig:
    @patch.dict("os.environ", {"ELEVENLABS_API_KEY": "", "ELEVENLABS_VOICE_ID": ""})
    @patch("applypilot.voice.load_env")
    def test_missing_api_key(self, mock_env):
        from applypilot.voice import _get_elevenlabs_config
        with pytest.raises(ValueError, match="ELEVENLABS_API_KEY"):
            _get_elevenlabs_config()

    @patch.dict("os.environ", {"ELEVENLABS_API_KEY": "key123", "ELEVENLABS_VOICE_ID": ""})
    @patch("applypilot.voice.load_env")
    def test_missing_voice_id(self, mock_env):
        from applypilot.voice import _get_elevenlabs_config
        with pytest.raises(ValueError, match="ELEVENLABS_VOICE_ID"):
            _get_elevenlabs_config()

    @patch.dict("os.environ", {"ELEVENLABS_API_KEY": "key123", "ELEVENLABS_VOICE_ID": "voice456"})
    @patch("applypilot.voice.load_env")
    def test_valid_config(self, mock_env):
        from applypilot.voice import _get_elevenlabs_config
        key, voice = _get_elevenlabs_config()
        assert key == "key123"
        assert voice == "voice456"


# ---------------------------------------------------------------------------
# Tests: generate_pitch (full flow)
# ---------------------------------------------------------------------------

class TestGeneratePitch:
    @patch("applypilot.voice.synthesize_audio")
    @patch("applypilot.voice.generate_pitch_script", return_value="Hey, great pitch here.")
    @patch("applypilot.voice.load_profile")
    def test_full_flow(self, mock_profile, mock_script, mock_audio, sample_profile, sample_job, tmp_path):
        mock_profile.return_value = sample_profile
        mock_audio.return_value = tmp_path / "fake.mp3"

        import applypilot.voice as voice_mod
        original_dir = voice_mod.PITCH_DIR
        voice_mod.PITCH_DIR = tmp_path / "pitches"

        try:
            from applypilot.voice import generate_pitch
            result = generate_pitch(sample_job, sample_profile)

            assert result["script"] == "Hey, great pitch here."
            assert result["title"] == "Senior Backend Engineer"
            assert result["site"] == "Stripe"
            assert "script_path" in result
        finally:
            voice_mod.PITCH_DIR = original_dir

    @patch("applypilot.voice.synthesize_audio", side_effect=ValueError("no API key"))
    @patch("applypilot.voice.generate_pitch_script", return_value="Pitch text.")
    @patch("applypilot.voice.load_profile")
    def test_audio_failure_graceful(self, mock_profile, mock_script, mock_audio, sample_profile, sample_job, tmp_path):
        mock_profile.return_value = sample_profile

        import applypilot.voice as voice_mod
        original_dir = voice_mod.PITCH_DIR
        voice_mod.PITCH_DIR = tmp_path / "pitches"

        try:
            from applypilot.voice import generate_pitch
            result = generate_pitch(sample_job, sample_profile)

            assert result["script"] == "Pitch text."
            assert result["audio_path"] is None  # Graceful fallback
            assert result["script_path"] is not None
        finally:
            voice_mod.PITCH_DIR = original_dir
