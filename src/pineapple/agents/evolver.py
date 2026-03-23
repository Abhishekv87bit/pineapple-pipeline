"""Stage 9: Evolver — session wrap-up and knowledge capture.

Pure Python — no LLM calls. LLM calls for Mem0/DSPy come in Phase 4.
"""
from __future__ import annotations

from datetime import datetime, timezone

from pineapple.models import EvolveReport
from pineapple.state import PipelineState


def evolve_node(state: PipelineState) -> dict:
    """Print session summary and return EvolveReport with stubs for future integrations.

    Future Phase 4 additions:
    - Mem0: Extract and store memories from the session
    - Neo4j: Update knowledge graph with project relationships
    - DSPy: Optimize prompts based on session outcomes
    """
    project_name = state.get("project_name", "unknown")
    run_id = state.get("run_id", "unknown")
    print(f"[Stage 9: Evolve] Project: {project_name}, Run: {run_id}")

    # Session summary
    errors = state.get("errors", [])
    cost = state.get("cost_total_usd", 0.0)
    path = state.get("path", "unknown")

    print(f"  [Evolve] Path: {path}")
    print(f"  [Evolve] Total cost: ${cost:.4f}")
    print(f"  [Evolve] Errors encountered: {len(errors)}")

    # Review what happened
    build_results = state.get("build_results", [])
    verify_record = state.get("verify_record")
    review_result = state.get("review_result")
    ship_result = state.get("ship_result")

    decisions: list[str] = []

    if build_results:
        completed = sum(1 for r in build_results if r.get("status") == "completed")
        decisions.append(f"Built {completed}/{len(build_results)} tasks successfully")

    if verify_record:
        all_green = verify_record.get("all_green", False)
        decisions.append(f"Verification: {'passed' if all_green else 'had issues'}")

    if review_result:
        decisions.append(f"Review verdict: {review_result.get('verdict', 'unknown')}")

    if ship_result:
        decisions.append(f"Ship action: {ship_result.get('action', 'unknown')}")

    # Stub session handoff path
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    handoff_path = f"sessions/{timestamp}-{project_name}.md"

    report = EvolveReport(
        session_handoff_path=handoff_path,
        bible_updated=False,
        decisions_logged=decisions,
        memory_extractions=[],  # Phase 4: Mem0 will populate this
    )

    print(f"  [Evolve] Session handoff: {report.session_handoff_path}")
    print(f"  [Evolve] Decisions logged: {len(report.decisions_logged)}")
    print("  [Evolve] Mem0/Neo4j/DSPy: stubbed (Phase 4)")
    print(f"  [Evolve] Pipeline complete.")

    return {
        "current_stage": "evolve",
        "evolve_report": report.model_dump(),
    }
