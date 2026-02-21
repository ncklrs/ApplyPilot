"""
Unified LLM client for ApplyPilot.

Auto-detects provider from environment:
  GEMINI_API_KEY    -> Google Gemini (default: gemini-2.0-flash)
  OPENAI_API_KEY    -> OpenAI (default: gpt-4o-mini)
  ANTHROPIC_API_KEY -> Anthropic Claude (default: claude-sonnet-4-20250514)
  LLM_URL           -> Local llama.cpp / Ollama compatible endpoint

LLM_MODEL env var overrides the model name for any provider.
MAX_LLM_CALLS env var or set_budget() caps total LLM invocations per process.
"""

import logging
import os
import threading
import time

import httpx

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Provider detection
# ---------------------------------------------------------------------------

_GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
_OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "")
_ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
_LOCAL_URL = os.environ.get("LLM_URL", "")


def _detect_provider() -> tuple[str, str, str]:
    """Return (base_url, model, api_key) based on environment variables."""
    model_override = os.environ.get("LLM_MODEL", "")

    if _GEMINI_KEY and not _LOCAL_URL:
        return (
            "https://generativelanguage.googleapis.com/v1beta/openai",
            model_override or "gemini-2.0-flash",
            _GEMINI_KEY,
        )

    if _OPENAI_KEY and not _LOCAL_URL:
        return (
            "https://api.openai.com/v1",
            model_override or "gpt-4o-mini",
            _OPENAI_KEY,
        )

    if _ANTHROPIC_KEY and not _LOCAL_URL:
        return (
            "https://api.anthropic.com",
            model_override or "claude-sonnet-4-20250514",
            _ANTHROPIC_KEY,
        )

    if _LOCAL_URL:
        return (
            _LOCAL_URL.rstrip("/"),
            model_override or "local-model",
            os.environ.get("LLM_API_KEY", ""),
        )

    raise RuntimeError(
        "No LLM provider configured. "
        "Set GEMINI_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY, or LLM_URL in your environment."
    )


# ---------------------------------------------------------------------------
# Budget tracking
# ---------------------------------------------------------------------------

_budget_lock = threading.Lock()
_call_count = 0
_max_calls = int(os.environ.get("MAX_LLM_CALLS", "0"))  # 0 = unlimited


def set_budget(max_calls: int) -> None:
    """Set maximum LLM calls for this process. 0 = unlimited."""
    global _max_calls
    with _budget_lock:
        _max_calls = max_calls


def get_call_count() -> int:
    """Return the number of LLM calls made so far."""
    return _call_count


def _check_budget() -> None:
    """Raise RuntimeError if the LLM call budget is exhausted."""
    global _call_count
    with _budget_lock:
        if _max_calls > 0 and _call_count >= _max_calls:
            raise RuntimeError(
                f"LLM call budget exhausted ({_call_count}/{_max_calls} calls). "
                "Increase MAX_LLM_CALLS or use --max-llm-calls to raise the limit."
            )
        _call_count += 1


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

_MAX_RETRIES = 3
_TIMEOUT = 120  # seconds


class LLMClient:
    """Unified LLM client supporting OpenAI-compatible and Anthropic APIs."""

    def __init__(self, base_url: str, model: str, api_key: str) -> None:
        self.base_url = base_url
        self.model = model
        self.api_key = api_key
        self._is_anthropic = "anthropic.com" in base_url
        self._client = httpx.Client(timeout=_TIMEOUT)

    # -- public API ---------------------------------------------------------

    def chat(
        self,
        messages: list[dict],
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> str:
        """Send a chat completion request and return the assistant message text."""
        _check_budget()

        if self._is_anthropic:
            return self._chat_anthropic(messages, temperature, max_tokens)
        return self._chat_openai(messages, temperature, max_tokens)

    def ask(self, prompt: str, **kwargs) -> str:
        """Convenience: single user prompt -> assistant response."""
        return self.chat([{"role": "user", "content": prompt}], **kwargs)

    def close(self) -> None:
        self._client.close()

    # -- OpenAI-compatible backend ------------------------------------------

    def _chat_openai(
        self,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
    ) -> str:
        """Send request via OpenAI-compatible chat/completions endpoint."""
        # Qwen3 optimization: prepend /no_think to skip chain-of-thought
        # reasoning, saving tokens on structured extraction tasks.
        if "qwen" in self.model.lower() and messages:
            first = messages[0]
            if first.get("role") == "user" and not first["content"].startswith("/no_think"):
                messages = [{"role": first["role"], "content": f"/no_think\n{first['content']}"}] + messages[1:]

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        for attempt in range(_MAX_RETRIES):
            try:
                resp = self._client.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                )
                if resp.status_code in (429, 503) and attempt < _MAX_RETRIES - 1:
                    wait = 2 ** attempt
                    log.warning(
                        "LLM returned %s, retrying in %ds (attempt %d/%d)",
                        resp.status_code, wait, attempt + 1, _MAX_RETRIES,
                    )
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"]
            except httpx.TimeoutException:
                if attempt < _MAX_RETRIES - 1:
                    wait = 2 ** attempt
                    log.warning(
                        "LLM request timed out, retrying in %ds (attempt %d/%d)",
                        wait, attempt + 1, _MAX_RETRIES,
                    )
                    time.sleep(wait)
                    continue
                raise

        raise RuntimeError("LLM request failed after all retries")

    # -- Anthropic Messages API backend -------------------------------------

    def _chat_anthropic(
        self,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
    ) -> str:
        """Send request via Anthropic's /v1/messages endpoint.

        Differences from OpenAI:
        - Auth via x-api-key header (not Bearer)
        - Requires anthropic-version header
        - System message extracted to top-level 'system' field
        - Response at content[0].text (not choices[0].message.content)
        """
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }

        # Extract system message from messages list into top-level field
        system_text = None
        user_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system_text = msg["content"]
            else:
                user_messages.append(msg)

        payload: dict = {
            "model": self.model,
            "messages": user_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if system_text:
            payload["system"] = system_text

        for attempt in range(_MAX_RETRIES):
            try:
                resp = self._client.post(
                    f"{self.base_url}/v1/messages",
                    json=payload,
                    headers=headers,
                )
                if resp.status_code in (429, 503, 529) and attempt < _MAX_RETRIES - 1:
                    wait = 2 ** attempt
                    log.warning(
                        "Anthropic returned %s, retrying in %ds (attempt %d/%d)",
                        resp.status_code, wait, attempt + 1, _MAX_RETRIES,
                    )
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                data = resp.json()
                return data["content"][0]["text"]
            except httpx.TimeoutException:
                if attempt < _MAX_RETRIES - 1:
                    wait = 2 ** attempt
                    log.warning(
                        "Anthropic request timed out, retrying in %ds (attempt %d/%d)",
                        wait, attempt + 1, _MAX_RETRIES,
                    )
                    time.sleep(wait)
                    continue
                raise

        raise RuntimeError("Anthropic request failed after all retries")


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_instance: LLMClient | None = None


def get_client() -> LLMClient:
    """Return (or create) the module-level LLMClient singleton."""
    global _instance
    if _instance is None:
        base_url, model, api_key = _detect_provider()
        log.info("LLM provider: %s  model: %s", base_url, model)
        _instance = LLMClient(base_url, model, api_key)
    return _instance
