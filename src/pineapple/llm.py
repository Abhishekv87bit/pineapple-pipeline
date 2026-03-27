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

import logging
import os
import time
from typing import Any

import instructor

# ---------------------------------------------------------------------------
# Optional LangFuse integration (graceful degradation if not installed)
# ---------------------------------------------------------------------------

_HAS_LANGFUSE = False
try:
    from langfuse import Langfuse
    _HAS_LANGFUSE = True
except ImportError:
    pass

_logger = logging.getLogger(__name__)

_langfuse_client = None


def get_langfuse():
    """Return a singleton LangFuse client, or None if unavailable.

    Reads LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, and LANGFUSE_HOST
    from environment variables automatically.
    """
    global _langfuse_client
    if _langfuse_client is None and _HAS_LANGFUSE:
        try:
            _langfuse_client = Langfuse()
        except Exception as exc:
            _logger.debug("LangFuse init failed: %s", exc)
    return _langfuse_client


def flush_traces() -> None:
    """Flush any pending LangFuse traces.  Safe to call even without LangFuse."""
    lf = get_langfuse()
    if lf is not None:
        try:
            lf.flush()
        except Exception as exc:
            _logger.debug("LangFuse flush failed: %s", exc)

# ---------------------------------------------------------------------------
# Provider constants
# ---------------------------------------------------------------------------

PROVIDER_GEMINI = "gemini"
PROVIDER_CLAUDE = "claude"

# Default models per provider
_DEFAULT_MODELS: dict[str, str] = {
    PROVIDER_GEMINI: os.environ.get("PINEAPPLE_MODEL_GEMINI", "gemini-2.5-flash"),
    PROVIDER_CLAUDE: os.environ.get("PINEAPPLE_MODEL_CLAUDE", "claude-sonnet-4-20250514"),
}

# Rough cost estimates per call (USD) -- used for cost tracking
COST_ESTIMATES: dict[str, float] = {
    PROVIDER_GEMINI: 0.001,  # approximate per-call cost (free tier is not truly $0)
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

def _make_gemini_client(mode: instructor.Mode | None = None) -> instructor.Instructor:
    """Create an Instructor-patched Gemini client using google.genai.

    Args:
        mode: Instructor mode override. Defaults to GENAI_STRUCTURED_OUTPUTS.
              Use GENAI_TOOLS for stages with complex nested Pydantic models
              that Gemini's structured output mode can't handle reliably.
    """
    from google import genai

    api_key = _get_gemini_api_key()
    if not api_key:
        raise ValueError("Gemini API key not found (GOOGLE_API_KEY / GEMINI_API_KEY)")

    raw_client = genai.Client(api_key=api_key)
    effective_mode = mode or instructor.Mode.GENAI_STRUCTURED_OUTPUTS
    return instructor.from_genai(raw_client, mode=effective_mode)


def _make_claude_client() -> instructor.Instructor:
    """Create an Instructor-patched Anthropic client."""
    from anthropic import Anthropic

    return instructor.from_anthropic(Anthropic())


# ---------------------------------------------------------------------------
# Token usage extraction helpers
# ---------------------------------------------------------------------------


def _extract_usage(result: Any, provider: str) -> dict[str, int] | None:
    """Try to pull real token counts from an Instructor/Pydantic response.

    Instructor stores the raw API response on `result._raw_response` for
    both Anthropic and Gemini.  We try several common attribute paths.

    Returns a dict like {"input": 1200, "output": 340, "total": 1540}
    or None if we cannot determine usage.
    """
    raw = getattr(result, "_raw_response", None)

    # Anthropic: raw.usage.input_tokens / output_tokens
    if raw is not None:
        usage_obj = getattr(raw, "usage", None)
        if usage_obj is not None:
            inp = getattr(usage_obj, "input_tokens", 0) or 0
            out = getattr(usage_obj, "output_tokens", 0) or 0
            if inp or out:
                return {"input": inp, "output": out, "total": inp + out}

    # Gemini: raw.usage_metadata.prompt_token_count / candidates_token_count
    if raw is not None:
        meta = getattr(raw, "usage_metadata", None)
        if meta is not None:
            inp = getattr(meta, "prompt_token_count", 0) or 0
            out = getattr(meta, "candidates_token_count", 0) or 0
            if inp or out:
                return {"input": inp, "output": out, "total": inp + out}

    return None


def estimate_cost(provider: str, usage: dict[str, int] | None = None) -> float:
    """Return estimated cost in USD for a single LLM call.

    If *usage* contains real token counts, compute cost from per-token rates.
    Otherwise fall back to the flat COST_ESTIMATES dict.
    """
    if usage is not None:
        inp = usage.get("input", 0)
        out = usage.get("output", 0)
        if provider == PROVIDER_CLAUDE:
            # Claude Sonnet 4: $3/1M input, $15/1M output
            return (inp * 3.0 + out * 15.0) / 1_000_000
        if provider == PROVIDER_GEMINI:
            # Gemini 2.5 Flash: essentially free tier / negligible
            return (inp * 0.15 + out * 0.60) / 1_000_000
    return COST_ESTIMATES.get(provider, 0.02)


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

    def __init__(self, client: instructor.Instructor, model: str, provider: str, stage: str | None = None):
        self._client = client
        self.model = model
        self.provider = provider
        self._stage = stage

    def create(
        self,
        response_model: type[Any],
        messages: list[dict[str, str]],
        system: str = "",
        max_tokens: int = 4096,
        stage: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """Call the underlying LLM and return a Pydantic model instance.

        Args:
            response_model: The Pydantic model class to parse the response into.
            messages: List of message dicts with "role" and "content" keys.
            system: System prompt (handled identically by both providers via instructor).
            max_tokens: Maximum output tokens. Mapped to the correct param per provider.
            stage: Optional pipeline stage name for LangFuse trace metadata.
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

        # ------------------------------------------------------------------
        # LangFuse: open a generation span (if available)
        # LangFuse v4 API: use start_observation(as_type='generation')
        # instead of the v2 lf.trace() / trace.generation() pattern.
        # ------------------------------------------------------------------
        effective_stage = stage or self._stage
        lf = get_langfuse()
        generation = None
        if lf is not None:
            try:
                trace_name = f"pineapple:{effective_stage}" if effective_stage else "pineapple:call"
                # Build a compact input representation for the generation span
                gen_input = {"system": system, "messages": messages} if system else {"messages": messages}
                generation = lf.start_observation(
                    name=trace_name,
                    as_type="generation",
                    model=self.model,
                    input=gen_input,
                    model_parameters={"max_tokens": max_tokens},
                    metadata={
                        "provider": self.provider,
                        "stage": effective_stage or "unknown",
                        "response_model": response_model.__name__,
                    },
                )
            except Exception as exc:
                _logger.debug("LangFuse trace start failed: %s", exc)
                generation = None

        # ------------------------------------------------------------------
        # Actual LLM call
        # ------------------------------------------------------------------
        t0 = time.monotonic()
        error_obj = None
        result = None
        try:
            if self.provider == PROVIDER_CLAUDE:
                call_kwargs["max_tokens"] = max_tokens
            # Use thread-based timeout to prevent Gemini from hanging indefinitely
            from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
            _CALL_TIMEOUT = int(os.environ.get("PINEAPPLE_LLM_TIMEOUT", "120"))
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(self._client.messages.create, **call_kwargs)
                try:
                    result = future.result(timeout=_CALL_TIMEOUT)
                except FuturesTimeout:
                    raise TimeoutError(
                        f"LLM call timed out after {_CALL_TIMEOUT}s "
                        f"(provider={self.provider}, model={self.model}). "
                        f"Set PINEAPPLE_LLM_TIMEOUT to increase."
                    )
            return result
        except Exception as exc:
            error_obj = exc
            raise
        finally:
            elapsed = time.monotonic() - t0
            # Record the generation result in LangFuse
            if generation is not None:
                try:
                    # LangFuse v4: call generation.update() to set output/usage,
                    # then generation.end() to close the span.
                    # (generation.end() accepts no data kwargs in v4.)
                    update_kwargs: dict[str, Any] = {
                        "metadata": {"elapsed_seconds": round(elapsed, 3)},
                    }
                    if error_obj is not None:
                        update_kwargs["status_message"] = str(error_obj)
                        update_kwargs["level"] = "ERROR"
                    else:
                        update_kwargs["output"] = (
                            result.model_dump() if hasattr(result, "model_dump") else str(result)
                        )
                        # Try to extract real token usage from the underlying response
                        usage = _extract_usage(result, self.provider)
                        if usage:
                            # v4 uses usage_details (dict[str, int])
                            update_kwargs["usage_details"] = usage
                    generation.update(**update_kwargs)
                    generation.end()
                except Exception as exc:
                    _logger.debug("LangFuse generation end failed: %s", exc)

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
        # Architecture stage uses GENAI_TOOLS mode because DesignSpec has
        # nested ComponentSpec lists that GENAI_STRUCTURED_OUTPUTS can't
        # handle reliably with Gemini.
        gemini_mode = None  # default = GENAI_STRUCTURED_OUTPUTS
        if stage in ("architecture", "strategic_review"):
            gemini_mode = instructor.Mode.GENAI_TOOLS
        client = _make_gemini_client(mode=gemini_mode)
    else:
        client = _make_claude_client()

    model_name = model or _DEFAULT_MODELS[provider]

    return LLMClient(client=client, model=model_name, provider=provider, stage=stage)


def call_with_retry(
    stage: str,
    response_model: type[Any],
    system: str,
    messages: list[dict[str, str]],
    max_tokens: int = 4096,
    max_retries: int = 3,
    client: "LLMClient | None" = None,
) -> tuple[Any, str, float]:
    """Call the LLM with cost tracking.

    Retry is handled by the underlying SDKs:
    - Anthropic SDK: retries HTTP 429/500/503 automatically (``max_retries``
      on the client constructor).
    - Google GenAI SDK: retries server errors with exponential backoff.
    - Instructor: retries validation errors when ``max_retries`` is passed
      to ``.create()``.

    This function adds cost tracking on top of the native retry behaviour.

    Args:
        stage: Pipeline stage name (for provider routing and LangFuse).
        response_model: Pydantic model class for Instructor to parse into.
        system: System prompt.
        messages: List of message dicts.
        max_tokens: Max output tokens.
        max_retries: Instructor validation retries (default 3).
        client: Optional pre-created LLMClient to reuse (e.g. builder reuses
                one client across all tasks). If None, a new client is created.

    Returns:
        Tuple of (parsed_result, provider_name, cost_usd).
    """
    llm = client if client is not None else get_llm_client(stage=stage)

    from pineapple.spinner import Spinner

    # Retry with exponential backoff for rate-limit (429) errors.
    # Instructor retries validation errors instantly; this outer loop
    # handles API-level throttling that needs a cooldown.
    _RATE_LIMIT_RETRIES = int(os.environ.get("PINEAPPLE_RATE_LIMIT_RETRIES", "5"))
    last_error = None
    for attempt in range(_RATE_LIMIT_RETRIES):
        try:
            with Spinner(f"Calling {llm.provider}..."):
                result = llm.create(
                    response_model=response_model,
                    system=system,
                    messages=messages,
                    max_tokens=max_tokens,
                    max_retries=max_retries,
                )
            usage = _extract_usage(result, llm.provider)
            cost = estimate_cost(llm.provider, usage)
            flush_traces()
            return result, llm.provider, cost
        except Exception as exc:
            last_error = exc
            err_str = str(exc).lower()
            if "429" in err_str or "resource_exhausted" in err_str or "rate" in err_str:
                wait = min(30 * (2 ** attempt), 120)  # 30s, 60s, 120s, 120s, 120s
                print(f"  [LLM] Rate limited (attempt {attempt + 1}/{_RATE_LIMIT_RETRIES}), waiting {wait}s...")
                time.sleep(wait)
                continue
            raise  # Non-rate-limit error, don't retry

    raise last_error  # All retries exhausted


def has_any_llm_key() -> bool:
    """Check if at least one LLM API key is configured."""
    return _has_gemini() or _has_claude()
