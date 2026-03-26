"""Stage 9: Evolver — session wrap-up and knowledge capture."""
from __future__ import annotations

import os
from datetime import datetime, timezone

from pineapple.models import EvolveReport
from pineapple.state import PipelineState


def evolve_node(state: PipelineState) -> dict:
    """Print session summary and persist learnings to Mem0 and Neo4j."""
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
        memory_extractions=[],
    )

    print(f"  [Evolve] Session handoff: {report.session_handoff_path}")
    print(f"  [Evolve] Decisions logged: {len(report.decisions_logged)}")

    # --- Mem0: Store session memories ---
    memory_extractions = []
    mem0_key = os.environ.get("MEM0_API_KEY")
    if mem0_key:
        try:
            from mem0 import MemoryClient
            mem0 = MemoryClient(api_key=mem0_key)

            # Build memory text from session results
            memory_text = (
                f"Pipeline run for '{project_name}' ({path} path). "
                f"Cost: ${cost:.4f}. "
                f"{'Completed successfully' if not errors else f'{len(errors)} errors encountered'}. "
                f"{'; '.join(decisions)}"
            )

            mem0.add(
                messages=[{"role": "user", "content": memory_text}],
                user_id="pineapple-pipeline",
                metadata={"project": project_name, "run_id": run_id},
            )
            memory_extractions.append(memory_text)
            print("  [Evolve] Mem0: Stored session memory")
        except Exception as exc:
            print(f"  [Evolve] Mem0: Failed — {exc}")
    else:
        print("  [Evolve] Mem0: Skipped (MEM0_API_KEY not set)")

    # --- Neo4j: Update component graph ---
    neo4j_uri = os.environ.get("NEO4J_URI")
    neo4j_user = os.environ.get("NEO4J_USERNAME", "neo4j")
    neo4j_password = os.environ.get("NEO4J_PASSWORD")
    if neo4j_uri and neo4j_password:
        try:
            from neo4j import GraphDatabase
            driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_password))

            design_spec = state.get("design_spec") or {}
            components = design_spec.get("components", [])

            if components:
                with driver.session() as neo4j_session:
                    # Create project node
                    neo4j_session.run(
                        "MERGE (p:Project {name: $name}) SET p.last_run = $run_id, p.cost = $cost",
                        name=project_name, run_id=run_id, cost=cost,
                    )
                    # Create component nodes and relationships
                    for comp in components:
                        comp_name = comp.get("name", "unknown")
                        comp_desc = comp.get("description", "")
                        neo4j_session.run(
                            "MERGE (c:Component {name: $name}) SET c.description = $desc "
                            "WITH c "
                            "MATCH (p:Project {name: $project}) "
                            "MERGE (p)-[:HAS_COMPONENT]->(c)",
                            name=comp_name, desc=comp_desc, project=project_name,
                        )
                print(f"  [Evolve] Neo4j: Stored {len(components)} component(s) for '{project_name}'")
            else:
                print("  [Evolve] Neo4j: No components in design spec to store")

            driver.close()
        except Exception as exc:
            print(f"  [Evolve] Neo4j: Failed — {exc}")
    else:
        print("  [Evolve] Neo4j: Skipped (NEO4J_URI not set)")

    # Update report with real memory extractions
    report = EvolveReport(
        session_handoff_path=handoff_path,
        bible_updated=False,
        decisions_logged=decisions,
        memory_extractions=memory_extractions,
    )

    print(f"  [Evolve] Pipeline complete.")

    return {
        "current_stage": "evolve",
        "evolve_report": report.model_dump(),
    }
