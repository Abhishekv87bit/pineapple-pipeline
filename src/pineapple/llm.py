"""LLM provider router -- use Claude or Gemini interchangeably via Instructor.

This module is the single point of LLM access for the entire pipeline.
All agents call `get_llm_client()` to get an Instructor-patched client that
returns Pydantic models, regardless of whether the underlying provider is
Anthropic (Claude) or Google (Gemini).

Provider selection priority:
1. Stage-specific env var: PINEAPPLE_LLM_STAGE_<name>=claude
2. Global env var: PINEAPPLE_LLM=gemini (default)
3. Fallback: whichever provider has an API key available

Environment variables:
    PINEAPPLE_LLM           Global default provider: "gemini" (default) or "claude"
    PINEAPPLE_LLM_STAGE_*   Per-stage override, e.g. PINEAPPLE_LLM_STAGE_strategic_review=claude
    ANTHROPIC_API_KEY        Required for Claude
    GOOGLE_API_KEY           Required for Gemini (also checks GEMINI_API_KEY)
"""
from __future__ import annotations

import os
from typing import Any

import instructor

# ---------------------------------------------------------------------------
# Provider constants
# ---------------------------------------------------------------------------

PROVIDER_GEMINI = "gemini"
PROVIDER_CLAUDE = "claude"

# Default models per provider
_DEFAULT_MODELS: dict[str, str] = {
    PROVIDER_GEMINI: "gemini-2.5-flash",
    PROVIDER_CLAUDE: "claude-sonnet-4-20250514",
}

# Rough cost estimates per call (USD) -- used for cost tracking
COST_ESTIMATES: dict[str, float] = {
    PROVIDER_GEMINI: 0.0,   # free tier
    PROVIDER_CLAUDE: 0.02,  # ~$3/1M input, $15/1M output
}


# ---------------------------------------------------------------------------
# API key helpers
# ---------------------------------------------------------------------------

def _get_gemini_api_key() -> str | None:
    """Return the Gemini API key from env, checking both common var names."""
    return os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")


def _get_anthropic_api_key() -> str | None:
    """Return the Anthropic API key from env."""
    return os.environ.get("ANTHROPIC_API_KEY")


def _has_gemini() -> bool:
    return bool(_get_gemini_api_key())


def _has_claude() -> bool:
    return bool(_get_anthropic_api_key())


# ---------------------------------------------------------------------------
# Provider resolution
# ---------------------------------------------------------------------------

def _resolve_provider(stage: str | None = None) -> str:
    """Determine which provider to use based on env vars and fallbacks.

    Returns "gemini" or "claude".

    Raises ValueError if no provider has an API key configured.
    """
    # 1. Stage-specific override
    if stage:
        stage_var = f"PINEAPPLE_LLM_STAGE_{stage}"
        stage_override = os.environ.get(stage_var, "").strip().lower()
        if stage_override in (PROVIDER_GEMINI, PROVIDER_CLAUDE):
            return stage_override

    # 2. Global env var (default: gemini)
    global_pref = os.environ.get("PINEAPPLE_LLM", PROVIDER_GEMINI).strip().lower()
    if global_pref in (PROVIDER_GEMINI, PROVIDER_CLAUDE):
        preferred = global_pref
    else:
        preferred = PROVIDER_GEMINI

    # 3. Check if preferred provider has an API key; fall back to the other
    if preferred == PROVIDER_GEMINI and _has_gemini():
        return PROVIDER_GEMINI
    if preferred == PROVIDER_CLAUDE and _has_claude():
        return PROVIDER_CLAUDE

    # Fallback: try whichever has a key
    if _has_gemini():
        return PROVIDER_GEMINI
    if _has_claude():
        return PROVIDER_CLAUDE

    raise ValueError(
        "No LLM API key found. Set GOOGLE_API_KEY (or GEMINI_API_KEY) for Gemini, "
        "or ANTHROPIC_API_KEY for Claude."
    )


# ---------------------------------------------------------------------------
# Client factory
# ---------------------------------------------------------------------------

def _make_gemini_client() -> instructor.Instructor:
    """Create an Instructor-patched Gemini client using google.genai."""
    from google import genai

    api_key = _get_gemini_api_key()
    if not api_key:
        raise ValueError("Gemini API key not found (GOOGLE_API_KEY / GEMINI_API_KEY)")

    raw_client = genai.Client(api_key=api_key)
    return instructor.from_genai(raw_client)


def _make_claude_client() -> instructor.Instructor:
    """Create an Instructor-patched Anthropic client."""
    from anthropic import Anthropic

    return instructor.from_anthropic(Anthropic())


# ---------------------------------------------------------------------------
# Unified call wrapper
# ---------------------------------------------------------------------------

class LLMClient:
    """Thin wrapper that normalizes the Instructor call interface across providers.

    Both Anthropic and Gemini instructor clients accept `client.messages.create()`
    but they differ on some kwargs (e.g. Anthropic uses `max_tokens`, Gemini uses
    config-based max output tokens). This wrapper handles those differences so
    agents can use a single call pattern.

    Usage::

        llm = get_llm_client(stage="strategic_review")
        brief = llm.create(
            response_model=StrategicBrief,
            system="You are a CEO...",
            messages=[{"role": "user", "content": "..."}],
            max_tokens=4096,
        )
    """

    def __init__(self, client: instructor.Instructor, model: str, provider: str):
        self._client = client
        self.model = model
        self.provider = provider

    def create(
        self,
        response_model: type[Any],
        messages: list[dict[str, str]],
        system: str = "",
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> Any:
        """Call the underlying LLM and return a Pydantic model instance.

        Args:
            response_model: The Pydantic model class to parse the response into.
            messages: List of message dicts with "role" and "content" keys.
            system: System prompt (handled identically by both providers via instructor).
            max_tokens: Maximum output tokens. Mapped to the correct param per provider.
            **kwargs: Additional kwargs passed to the underlying client.

        Returns:
            An instance of response_model populated by the LLM.
        """
        call_kwargs: dict[str, Any] = {
            "model": self.model,
            "response_model": response_model,
            "messages": messages,
            **kwargs,
        }

        if system:
            call_kwargs["system"] = system

        if self.provider == PROVIDER_CLAUDE:
            call_kwargs["max_tokens"] = max_tokens
            # Anthropic instructor uses client.messages.create
            return self._client.messages.create(**call_kwargs)
        else:
            # Gemini: max_tokens is not a direct kwarg; instructor's genai handler
            # builds a config internally. We skip max_tokens to avoid passing an
            # unknown kwarg to google.genai.models.generate_content.
            return self._client.messages.create(**call_kwargs)

    def __repr__(self) -> str:
        return f"LLMClient(provider={self.provider!r}, model={self.model!r})"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_llm_client(
    stage: str | None = None,
    model: str | None = None,
) -> LLMClient:
    """Get a unified LLM client for the pipeline.

    This is the ONLY function agents should call to get an LLM client.

    Args:
        stage: Optional stage name for per-stage provider override.
               E.g. "strategic_review", "architecture", "plan", "build", "review".
        model: Optional model name override. If None, uses the default for the
               resolved provider.

    Returns:
        An LLMClient wrapping an Instructor-patched provider client.

    Raises:
        ValueError: If no API key is available for any provider.
    """
    provider = _resolve_provider(stage)

    if provider == PROVIDER_GEMINI:
        client = _make_gemini_client()
    else:
        client = _make_claude_client()

    model_name = model or _DEFAULT_MODELS[provider]

    return LLMClient(client=client, model=model_name, provider=provider)


def has_any_llm_key() -> bool:
    """Check if at least one LLM API key is configured."""
    return _has_gemini() or _has_claude()
