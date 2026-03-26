"""Gate functions for pipeline stage transitions.

Gates are pure Python functions that inspect state and return routing decisions.
No LLM calls. No side effects. Just deterministic logic.
"""
import os

from pineapple.state import PipelineState

# ---------------------------------------------------------------------------
# Routing gates (return a string label for conditional edges)
# ---------------------------------------------------------------------------


def route_by_path(state: PipelineState) -> str:
    """Route the pipeline based on the selected path.

    Returns:
        "strategic_review" for full path,
        "plan" for medium path,
        "setup" for lightweight path.
    """
    mapping = {
        "full": "strategic_review",
        "medium": "plan",
        "lightweight": "setup",
    }
    path = state.get("path", "full")
    return mapping.get(path, "strategic_review")


def review_gate(state: PipelineState, max_attempts: int = 5) -> str:
    """Decide what happens after the review stage.

    Returns:
        "pass"  -> proceed to ship
        "retry" -> loop back to build
        "fail"  -> escalate to human intervention
    """
    # Hard ceiling on total build attempts
    attempt_counts = state.get("attempt_counts", {})
    if attempt_counts.get("build", 0) >= max_attempts:
        return "fail"

    # Cost ceiling
    if state.get("cost_total_usd", 0.0) > float(os.environ.get("PINEAPPLE_COST_CEILING", "200.0")):
        return "fail"

    # Check review result for critical issues
    review_result = state.get("review_result")
    if review_result is not None:
        if review_result.get("critical_issues", []):
            return "retry"

    return "pass"


# ---------------------------------------------------------------------------
# Boolean gates (return True when the stage is complete and ready to proceed)
# ---------------------------------------------------------------------------


def intake_gate(state: PipelineState) -> bool:
    """Intake is complete when a context bundle has been produced."""
    return state.get("context_bundle") is not None


def strategic_review_gate(state: PipelineState) -> bool:
    """Strategic review requires a brief AND human approval."""
    return (
        state.get("strategic_brief") is not None
        and state.get("human_approvals", {}).get("strategic_review") is True
    )


def architecture_gate(state: PipelineState) -> bool:
    """Architecture requires a design spec AND human approval."""
    return (
        state.get("design_spec") is not None
        and state.get("human_approvals", {}).get("architecture") is True
    )


def plan_gate(state: PipelineState) -> bool:
    """Plan requires a task plan AND human approval."""
    return (
        state.get("task_plan") is not None
        and state.get("human_approvals", {}).get("plan") is True
    )


def setup_gate(state: PipelineState) -> bool:
    """Setup is complete when workspace info is available."""
    return state.get("workspace_info") is not None


def build_gate(state: PipelineState) -> bool:
    """Build is complete when at least one build result exists."""
    results = state.get("build_results", [])
    return len(results) > 0


def verify_gate(state: PipelineState) -> bool:
    """Verify passes when all checks are green."""
    record = state.get("verify_record")
    if record is None:
        return False
    return record.get("all_green") is True


def ship_gate(state: PipelineState) -> bool:
    """Ship is complete when a ship result exists."""
    return state.get("ship_result") is not None
