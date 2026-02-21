"""Tests for Claude Code agent mode."""

import pytest
from unittest.mock import patch, MagicMock
from applypilot.scoring.agent import is_agent_available, _run_claude_agent


class TestIsAgentAvailable:
    """Test agent availability detection."""

    @patch("shutil.which", return_value="/usr/local/bin/claude")
    def test_available(self, mock_which):
        assert is_agent_available() is True

    @patch("shutil.which", return_value=None)
    def test_not_available(self, mock_which):
        assert is_agent_available() is False


class TestRunClaudeAgent:
    """Test agent subprocess spawning."""

    @patch("subprocess.Popen")
    def test_successful_run(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (
            '{"result": "test output", "total_cost_usd": 0.01, "num_turns": 3}',
            "",
        )
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc

        result = _run_claude_agent("test prompt")
        assert result["status"] == "ok"
        assert result["output"] == "test output"
        assert result["cost_usd"] == 0.01
        assert result["turns"] == 3

    @patch("subprocess.Popen")
    def test_failed_run(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = ("", "error")
        mock_proc.returncode = 1
        mock_popen.return_value = mock_proc

        result = _run_claude_agent("test prompt")
        assert result["status"] == "error"

    @patch("subprocess.Popen")
    def test_timeout(self, mock_popen):
        import subprocess
        mock_proc = MagicMock()
        mock_proc.communicate.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=180)
        mock_proc.kill = MagicMock()
        mock_proc.wait = MagicMock()
        mock_popen.return_value = mock_proc

        result = _run_claude_agent("test prompt", timeout=180)
        assert result["status"] == "error"
        assert "timed out" in result["output"]
        mock_proc.kill.assert_called_once()

    @patch("subprocess.Popen")
    def test_invalid_json_output(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = ("not json", "")
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc

        result = _run_claude_agent("test prompt")
        assert result["status"] == "ok"
        # Should handle gracefully with raw output
        assert result["raw"] == "not json"

    @patch("subprocess.Popen")
    def test_command_args(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = ('{"result": "ok"}', "")
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc

        _run_claude_agent("test", model="opus", allowed_tools="Read,Write")

        cmd = mock_popen.call_args[0][0]
        assert "claude" in cmd
        assert "--model" in cmd
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "opus"
        assert "--allowedTools" in cmd
        idx = cmd.index("--allowedTools")
        assert cmd[idx + 1] == "Read,Write"
