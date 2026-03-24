"""Stage 3: Plan — Break the design into ordered implementation tasks.

Uses the LLM router to generate a structured TaskPlan via Instructor.
Install dependencies with: pip install 'pineapple-pipeline[llm]'
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from pineapple.models import TaskPlan
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
You are a senior technical project planner. Given an architectural design \
specification, you break it down into discrete, ordered implementation tasks.

Rules:
- Each task must be independently describable and testable.
- Order tasks by dependency: foundational work first (models, schemas, config), \
then core logic, then integration, then tests, then polish.
- For each task, list the files it will create and/or modify.
- Classify complexity as "trivial" (< 30 min), "standard" (1-3 hours), or \
"complex" (3+ hours).
- Estimate cost in USD for each task (LLM API calls needed to implement it).
- Keep task count reasonable: 3-15 tasks for most projects.
- Task IDs should be sequential: T1, T2, T3, etc.
- Sum all task costs into total_estimated_cost_usd.
- Set approved to false (human approves at the gate)."""

_USER_PROMPT_TEMPLATE = """\
Design Specification:
{design_spec_json}

Strategic Brief (for context):
{strategic_brief_json}

Project: {project_name}
Request: {request}

Break this design into an ordered list of implementation tasks. \
Each task should have a clear id, description, files_to_create, \
files_to_modify, complexity, and estimated_cost_usd. \
Order by dependency — foundational tasks first."""


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


def _build_user_prompt(state: PipelineState) -> str:
    """Construct the user prompt from pipeline state."""
    design_spec = state.get("design_spec") or {}
    strategic_brief = state.get("strategic_brief") or {}
    project_name = state.get("project_name", "unknown")
    request = state.get("request", "")

    return _USER_PROMPT_TEMPLATE.format(
        design_spec_json=json.dumps(design_spec, indent=2),
        strategic_brief_json=json.dumps(strategic_brief, indent=2),
        project_name=project_name,
        request=request,
    )


# ---------------------------------------------------------------------------
# LLM call with retry (only defined if deps are available)
# ---------------------------------------------------------------------------


def _call_llm(system: str, user: str) -> tuple[TaskPlan, str, float]:
    """Call the LLM via the router and return (TaskPlan, provider, cost_usd).

    Retries up to 3 times with exponential backoff for transient failures.
    Uses real token counts from the response when available, otherwise
    falls back to flat cost estimates.
    """
    llm = get_llm_client(stage="plan")

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=30))
    def _inner() -> TaskPlan:
        return llm.create(
            response_model=TaskPlan,
            system=system,
            messages=[{"role": "user", "content": user}],
            max_tokens=_MAX_TOKENS,
        )

    result = _inner()
    usage = _extract_usage(result, llm.provider)
    cost = estimate_cost(llm.provider, usage)
    return result, llm.provider, cost


# ---------------------------------------------------------------------------
# Error plan factory
# ---------------------------------------------------------------------------


def _make_error_plan(error: str) -> TaskPlan:
    """Create a stub TaskPlan for error cases."""
    return TaskPlan(
        tasks=[],
        total_estimated_cost_usd=0.0,
        approved=False,
    )


# ---------------------------------------------------------------------------
# Public node
# ---------------------------------------------------------------------------


def plan_node(state: PipelineState) -> dict:
    """Break the design specification into ordered implementation tasks.

    Takes the DesignSpec from Stage 2 (Architecture) and produces a TaskPlan
    with discrete, dependency-ordered tasks via Instructor + Anthropic API.

    Falls back gracefully if:
    - LLM dependencies (instructor, anthropic) are not installed
    - ANTHROPIC_API_KEY is not set
    - The LLM call fails after retries
    """
    project_name = state.get("project_name", "unknown")
    print(f"[Stage 3: Plan] Project: {project_name}")

    # --- Guard: missing LLM dependencies ---
    if not _HAS_LLM_DEPS:
        msg = (
            f"LLM dependencies not available ({_IMPORT_ERROR}). "
            "Install with: pip install 'pineapple-pipeline[llm]'"
        )
        print(f"  [Plan] {msg}")
        plan = _make_error_plan(msg)
        return {
            "current_stage": "plan",
            "task_plan": plan.model_dump(),
            "errors": state.get("errors", []) + [
                {"stage": "plan", "message": msg, "timestamp": datetime.now(timezone.utc).isoformat(), "recoverable": True},
            ],
        }

    # --- Guard: missing API key ---
    if not has_any_llm_key():
        msg = "No LLM API key set. Set GOOGLE_API_KEY (Gemini) or ANTHROPIC_API_KEY (Claude)."
        print(f"  [Plan] {msg}")
        plan = _make_error_plan(msg)
        return {
            "current_stage": "plan",
            "task_plan": plan.model_dump(),
            "errors": state.get("errors", []) + [
                {"stage": "plan", "message": msg, "timestamp": datetime.now(timezone.utc).isoformat(), "recoverable": True},
            ],
        }

    # --- Main path: call LLM ---
    try:
        user_prompt = _build_user_prompt(state)

        print("  [Plan] Calling LLM to generate task plan...")
        plan, provider, call_cost = _call_llm(_SYSTEM_PROMPT, user_prompt)

        # Force approved=False — human must approve at the interrupt gate
        plan.approved = False

        print(f"  [Plan] Task plan generated (provider: {provider}, cost: ${call_cost:.4f}):")
        print(f"    Tasks: {len(plan.tasks)}")
        for task in plan.tasks:
            print(f"    - {task.id}: {task.description} [{task.complexity}] ${task.estimated_cost_usd:.2f}")
        print(f"    Total estimated cost: ${plan.total_estimated_cost_usd:.2f}")

        # Flush LangFuse traces before returning
        flush_traces()

        return {
            "current_stage": "plan",
            "task_plan": plan.model_dump(),
            "cost_total_usd": state.get("cost_total_usd", 0.0) + call_cost,
        }

    except Exception as e:
        msg = f"LLM call failed after retries: {e}"
        print(f"  [Plan] ERROR: {msg}")
        plan = _make_error_plan(str(e))
        return {
            "current_stage": "plan",
            "task_plan": plan.model_dump(),
            "errors": state.get("errors", []) + [
                {"stage": "plan", "message": msg, "timestamp": datetime.now(timezone.utc).isoformat(), "recoverable": True},
            ],
        }
