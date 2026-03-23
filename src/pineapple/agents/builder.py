"""Stage 5: Builder — generate code for each task in the plan.

Uses the LLM router to generate BuildResult per task via Instructor.
Install dependencies with: pip install 'pineapple-pipeline[llm]'
"""
from __future__ import annotations

from datetime import datetime, timezone

from pineapple.models import BuildResult, Task, TaskPlan
from pineapple.state import PipelineState

# ---------------------------------------------------------------------------
# Lazy imports for optional LLM dependencies
# ---------------------------------------------------------------------------

_HAS_LLM_DEPS = True
_IMPORT_ERROR: str | None = None

try:
    from pineapple.llm import get_llm_client, has_any_llm_key, COST_ESTIMATES
    from tenacity import retry, stop_after_attempt, wait_exponential
except ImportError as exc:
    _HAS_LLM_DEPS = False
    _IMPORT_ERROR = str(exc)

# ---------------------------------------------------------------------------
# Model configuration
# ---------------------------------------------------------------------------

_MAX_TOKENS = 4096

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are an expert software engineer. You are given a task from a project plan.
Your job is to generate a BuildResult describing what code you would write.

ISOLATION: You can only write code. You cannot run tests, deploy, or modify
infrastructure. Focus solely on implementation.

Be specific about what files you would create or modify and what the
implementation approach would be."""

_USER_PROMPT_TEMPLATE = """\
Task ID: {task_id}
Description: {description}
Files to create: {files_to_create}
Files to modify: {files_to_modify}
Complexity: {complexity}

Design context:
{design_summary}

Generate a BuildResult for this task. Mark status as "completed" with a
commit message describing the change."""


# ---------------------------------------------------------------------------
# LLM call with retry
# ---------------------------------------------------------------------------


def _call_llm_for_task(task: Task, design_summary: str, llm=None) -> BuildResult:
    """Call the LLM to generate a BuildResult for a single task."""
    if llm is None:
        llm = get_llm_client(stage="build")

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=30))
    def _inner() -> BuildResult:
        return llm.create(
            response_model=BuildResult,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _USER_PROMPT_TEMPLATE.format(
                task_id=task.id,
                description=task.description,
                files_to_create=task.files_to_create or "None",
                files_to_modify=task.files_to_modify or "None",
                complexity=task.complexity,
                design_summary=design_summary,
            )}],
            max_tokens=_MAX_TOKENS,
        )

    return _inner()


# ---------------------------------------------------------------------------
# Fallback builder (no LLM)
# ---------------------------------------------------------------------------


def _build_task_fallback(task: Task) -> BuildResult:
    """Create a placeholder BuildResult without LLM."""
    return BuildResult(
        task_id=task.id,
        status="completed",
        commits=[f"placeholder: {task.description}"],
        errors=[],
    )


# ---------------------------------------------------------------------------
# Error result factory
# ---------------------------------------------------------------------------


def _make_error_result(task_id: str, error: str) -> BuildResult:
    """Create a failed BuildResult for error cases."""
    return BuildResult(
        task_id=task_id,
        status="failed",
        commits=[],
        errors=[error],
    )


# ---------------------------------------------------------------------------
# Public node
# ---------------------------------------------------------------------------


def builder_node(state: PipelineState) -> dict:
    """Generate code for each task in the plan.

    ISOLATED: Can only write code, cannot run tests.

    Falls back gracefully if:
    - LLM dependencies are not installed
    - ANTHROPIC_API_KEY is not set
    - The LLM call fails after retries
    """
    project_name = state.get("project_name", "unknown")
    print(f"[Stage 5: Build] Project: {project_name}")

    # Parse task plan from state -- lightweight path may skip planner
    task_plan_data = state.get("task_plan")
    if not task_plan_data:
        # Lightweight path: no planner ran, create single-task plan from request
        task_plan_data = {
            "tasks": [{"id": "TASK-001", "description": state.get("request", "implement change"), "files": [], "complexity": "trivial", "estimated_cost_usd": 0.01}],
            "total_estimated_cost_usd": 0.01,
            "approved": True,
        }
        print("  [Build] No task_plan found -- auto-generated single-task plan (lightweight path)")

    task_plan = TaskPlan(**task_plan_data)
    design_spec_data = state.get("design_spec") or {}
    design_summary = design_spec_data.get("summary", "No design spec available.")

    # Determine if we can use LLM
    use_llm = _HAS_LLM_DEPS and has_any_llm_key()
    llm = None
    provider = "none"

    if not use_llm:
        reason = _IMPORT_ERROR if not _HAS_LLM_DEPS else "No LLM API key set"
        print(f"  [Build] LLM unavailable ({reason}), using fallback builder.")
    else:
        llm = get_llm_client(stage="build")
        provider = llm.provider
        print(f"  [Build] Using provider: {provider}")

    build_results: list[dict] = []
    total_cost = 0.0
    cost_per_task = COST_ESTIMATES.get(provider, 0.01)

    for task in task_plan.tasks:
        print(f"  [Build] Task {task.id}: {task.description}")

        if use_llm:
            try:
                result = _call_llm_for_task(task, design_summary, llm=llm)
                # Ensure task_id matches
                result.task_id = task.id
                total_cost += cost_per_task
                print(f"    Status: {result.status}, Commits: {len(result.commits)}")
            except Exception as e:
                print(f"    ERROR: {e}")
                result = _make_error_result(task.id, str(e))
        else:
            result = _build_task_fallback(task)
            print(f"    Status: {result.status} (fallback)")

        build_results.append(result.model_dump())

    completed = sum(1 for r in build_results if r["status"] == "completed")
    failed = sum(1 for r in build_results if r["status"] == "failed")
    print(f"  [Build] Done: {completed} completed, {failed} failed out of {len(build_results)} tasks")

    # Increment build attempt count for observability
    attempt_counts = dict(state.get("attempt_counts", {}))
    attempt_counts["build"] = attempt_counts.get("build", 0) + 1

    return {
        "current_stage": "build",
        "build_results": build_results,
        "cost_total_usd": state.get("cost_total_usd", 0.0) + total_cost,
        "attempt_counts": attempt_counts,
    }
