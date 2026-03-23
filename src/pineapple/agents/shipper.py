"""Stage 8: Shipper — report what would be shipped.

Pure Python — no LLM calls.
"""
from __future__ import annotations

from pineapple.models import ShipResult
from pineapple.state import PipelineState


def ship_node(state: PipelineState) -> dict:
    """Print a summary of what was built, verified, and reviewed, then return ShipResult.

    Default action is "keep" (code stays on branch, no merge/PR yet).
    """
    project_name = state.get("project_name", "unknown")
    print(f"[Stage 8: Ship] Project: {project_name}")

    # Summarize build results
    build_results = state.get("build_results", [])
    completed = sum(1 for r in build_results if r.get("status") == "completed")
    failed = sum(1 for r in build_results if r.get("status") == "failed")
    print(f"  [Ship] Build: {completed} completed, {failed} failed out of {len(build_results)} tasks")

    # Summarize verification
    verify_record = state.get("verify_record")
    if verify_record:
        all_green = verify_record.get("all_green", False)
        layers = verify_record.get("layers", [])
        print(f"  [Ship] Verify: {'ALL GREEN' if all_green else 'ISSUES'} ({len(layers)} layers)")
    else:
        print("  [Ship] Verify: No verification record")

    # Summarize review
    review_result = state.get("review_result")
    if review_result:
        verdict = review_result.get("verdict", "unknown")
        print(f"  [Ship] Review verdict: {verdict}")
    else:
        print("  [Ship] Review: No review result")

    # Cost summary
    cost = state.get("cost_total_usd", 0.0)
    print(f"  [Ship] Total cost: ${cost:.4f}")

    result = ShipResult(
        action="keep",
        pr_url=None,
        merge_commit=None,
    )

    print(f"  [Ship] Action: {result.action}")

    return {
        "current_stage": "ship",
        "ship_result": result.model_dump(),
    }
