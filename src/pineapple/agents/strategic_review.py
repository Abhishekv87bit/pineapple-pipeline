"""Stage 1: Strategic Review — CEO-level strategic analysis of the project.

Uses the LLM router to generate a structured StrategicBrief via Instructor.
Install dependencies with: pip install 'pineapple-pipeline[llm]'
"""
from __future__ import annotations

from datetime import datetime, timezone

from pineapple.models import StrategicBrief
from pineapple.state import PipelineState

# ---------------------------------------------------------------------------
# Lazy imports for optional LLM dependencies
# ---------------------------------------------------------------------------

_HAS_LLM_DEPS = True
_IMPORT_ERROR: str | None = None

try:
    from pineapple.llm import get_llm_client, has_any_llm_key, COST_ESTIMATES, estimate_cost, _extract_usage, flush_traces
    from tenacity import retry, stop_after_attempt, wait_exponential
except ImportError as exc:
    _HAS_LLM_DEPS = False
    _IMPORT_ERROR = str(exc)

# ---------------------------------------------------------------------------
# Model configuration
# ---------------------------------------------------------------------------

_MAX_TOKENS = 4096

# ---------------------------------------------------------------------------
# System / user prompt templates
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a CEO-level strategic advisor reviewing a new project proposal.

Your job is to look BEYOND what was asked and find the REAL product. Apply the \
"Used Car Lot" principle:
- A used car lot thinks it sells cars. It actually sells financing, warranties, \
and peace of mind.
- What does the user THINK they're building?
- What are they ACTUALLY building (the deeper value)?
- What should they NOT build (scope traps)?

Cross-domain pattern matching: identify 2-3 adjacent domains where analogous \
problems have been solved, and weave those insights into your analysis.

Drive toward the 10-star version, then scale back to an achievable MVP.

Generate a Strategic Brief that would survive a board room presentation. \
Be specific and concrete, not generic."""

_USER_PROMPT_TEMPLATE = """\
Project Request: {request}

Context loaded: {context_files}
Project type: {project_type}

Generate a Strategic Brief for this project. Be specific, not generic.

For each field:
- **what**: One clear sentence describing what we are building.
- **why**: The REAL motivation — not the stated one. What hidden product did \
you find? Apply the Used Car Lot principle.
- **not_building**: Explicit scope exclusions. Things that might seem related \
but we deliberately omit in v1.
- **who_benefits**: Target users, stakeholders, and anyone affected. Include \
who might lose or be disrupted.
- **assumptions**: Things we are betting on being true. Each one is a risk if wrong.
- **open_questions**: Unresolved items that the Architecture stage must answer \
before design begins.
- **approved**: Always set to false (human approves at the gate)."""


# ---------------------------------------------------------------------------
# LLM call with retry (only defined if deps are available)
# ---------------------------------------------------------------------------


def _build_user_prompt(state: PipelineState) -> str:
    """Construct the user prompt from pipeline state."""
    request = state.get("request", "")
    context_bundle = state.get("context_bundle") or {}

    context_files = context_bundle.get("context_files", []) if context_bundle else []
    project_type = context_bundle.get("project_type", "unknown") if context_bundle else "unknown"

    return _USER_PROMPT_TEMPLATE.format(
        request=request,
        context_files=context_files if context_files else "None",
        project_type=project_type,
    )


def _call_llm(system: str, user: str) -> tuple[StrategicBrief, str, float]:
    """Call the LLM via the router and return (StrategicBrief, provider, cost_usd).

    Retries up to 3 times with exponential backoff for transient failures.
    Uses real token counts from the response when available, otherwise
    falls back to flat cost estimates.
    """
    llm = get_llm_client(stage="strategic_review")

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=30))
    def _inner() -> StrategicBrief:
        return llm.create(
            response_model=StrategicBrief,
            system=system,
            messages=[{"role": "user", "content": user}],
            max_tokens=_MAX_TOKENS,
        )

    result = _inner()
    usage = _extract_usage(result, llm.provider)
    cost = estimate_cost(llm.provider, usage)
    return result, llm.provider, cost


# ---------------------------------------------------------------------------
# Error brief factory
# ---------------------------------------------------------------------------


def _make_error_brief(error: str) -> StrategicBrief:
    """Create a stub StrategicBrief for error cases."""
    return StrategicBrief(
        what=f"Error generating brief: {error}",
        why="Strategic review failed",
        not_building=[],
        who_benefits="Unknown",
        assumptions=[f"LLM call failed: {error}"],
        open_questions=["Why did the strategic review fail?"],
        approved=False,
    )


# ---------------------------------------------------------------------------
# Public node
# ---------------------------------------------------------------------------


def strategic_review_node(state: PipelineState) -> dict:
    """Generate a CEO-level strategic review of the project request.

    This is the first node that makes real LLM calls. It produces a
    StrategicBrief artifact via Instructor + Anthropic API.

    Falls back gracefully if:
    - LLM dependencies (instructor, anthropic) are not installed
    - ANTHROPIC_API_KEY is not set
    - The LLM call fails after retries
    """
    project_name = state.get("project_name", "unknown")
    print(f"[Stage 1: Strategic Review] Project: {project_name}")

    # --- Guard: missing LLM dependencies ---
    if not _HAS_LLM_DEPS:
        msg = (
            f"LLM dependencies not available ({_IMPORT_ERROR}). "
            "Install with: pip install 'pineapple-pipeline[llm]'"
        )
        print(f"  [Strategic Review] {msg}")
        brief = _make_error_brief(msg)
        return {
            "current_stage": "strategic_review",
            "strategic_brief": brief.model_dump(),
            "errors": state.get("errors", []) + [
                {"stage": "strategic_review", "message": msg, "timestamp": datetime.now(timezone.utc).isoformat(), "recoverable": True},
            ],
        }

    # --- Guard: missing API key ---
    if not has_any_llm_key():
        msg = "No LLM API key set. Set GOOGLE_API_KEY (Gemini) or ANTHROPIC_API_KEY (Claude)."
        print(f"  [Strategic Review] {msg}")
        brief = _make_error_brief(msg)
        return {
            "current_stage": "strategic_review",
            "strategic_brief": brief.model_dump(),
            "errors": state.get("errors", []) + [
                {"stage": "strategic_review", "message": msg, "timestamp": datetime.now(timezone.utc).isoformat(), "recoverable": True},
            ],
        }

    # --- Main path: call LLM ---
    try:
        user_prompt = _build_user_prompt(state)

        print("  [Strategic Review] Calling LLM for strategic brief...")
        brief, provider, call_cost = _call_llm(_SYSTEM_PROMPT, user_prompt)

        # Force approved=False — human must approve at the interrupt gate
        brief.approved = False

        print(f"  [Strategic Review] Brief generated (provider: {provider}, cost: ${call_cost:.4f}):")
        print(f"    What: {brief.what}")
        print(f"    Why: {brief.why}")
        print(f"    Not building: {brief.not_building}")
        print(f"    Who benefits: {brief.who_benefits}")
        print(f"    Assumptions: {len(brief.assumptions)} items")
        print(f"    Open questions: {len(brief.open_questions)} items")

        # Flush LangFuse traces before returning
        flush_traces()

        return {
            "current_stage": "strategic_review",
            "strategic_brief": brief.model_dump(),
            "cost_total_usd": state.get("cost_total_usd", 0.0) + call_cost,
        }

    except Exception as e:
        msg = f"LLM call failed after retries: {e}"
        print(f"  [Strategic Review] ERROR: {msg}")
        brief = _make_error_brief(str(e))
        return {
            "current_stage": "strategic_review",
            "strategic_brief": brief.model_dump(),
            "errors": state.get("errors", []) + [
                {"stage": "strategic_review", "message": msg, "timestamp": datetime.now(timezone.utc).isoformat(), "recoverable": True},
            ],
        }
