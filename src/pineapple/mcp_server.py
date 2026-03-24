"""FastMCP server exposing the Pineapple Pipeline as MCP tools.

Start with:
    fastmcp run src/pineapple/mcp_server.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid

from fastmcp import FastMCP

mcp = FastMCP("pineapple-pipeline")

# Same default as cli.py
DEFAULT_DB_PATH = ".pineapple/checkpoints.db"


def _flush() -> None:
    """Flush LLM traces (LangFuse) if available. Safe no-op otherwise."""
    try:
        from pineapple.llm import flush_traces
        flush_traces()
    except Exception:
        pass


def _get_pipeline(db_path: str = DEFAULT_DB_PATH):
    """Create a compiled pipeline with SQLite checkpointer."""
    from pineapple.graph import create_pipeline
    return create_pipeline(db_path=db_path)


def _state_summary(state) -> dict:
    """Extract a JSON-serialisable summary from a LangGraph state snapshot."""
    if state.values is None or not state.values:
        return {"error": "No state found"}

    vals = state.values
    return {
        "run_id": vals.get("run_id", ""),
        "project_name": vals.get("project_name", ""),
        "path": vals.get("path", ""),
        "current_stage": vals.get("current_stage", ""),
        "waiting_before": list(state.next) if state.next else [],
        "human_approvals": vals.get("human_approvals", {}),
        "attempt_counts": vals.get("attempt_counts", {}),
        "cost_total_usd": vals.get("cost_total_usd", 0.0),
        "error_count": len(vals.get("errors", [])),
        "completed": not bool(state.next),
    }


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def pineapple_run(
    request: str,
    path: str = "full",
    project_name: str = "",
) -> str:
    """Start a new Pineapple Pipeline run.

    Args:
        request: What to build or fix (e.g. 'Build BrokerFlow auth module').
        path: Pipeline path - 'full', 'medium', or 'lightweight'.
        project_name: Optional project name (inferred from request if empty).

    Returns:
        JSON with run_id, current state, and what gate the pipeline is waiting at.
    """
    from pineapple.state import PipelineStage

    if path not in ("full", "medium", "lightweight"):
        return json.dumps({"error": f"Invalid path: {path}. Use full/medium/lightweight."})

    run_id = str(uuid.uuid4())

    initial_state = {
        "run_id": run_id,
        "request": request,
        "project_name": project_name,
        "branch": "",
        "path": path,
        "current_stage": PipelineStage.INTAKE.value,
        "context_bundle": None,
        "strategic_brief": None,
        "design_spec": None,
        "task_plan": None,
        "workspace_info": None,
        "build_results": [],
        "verify_record": None,
        "review_result": None,
        "ship_result": None,
        "evolve_report": None,
        "attempt_counts": {},
        "human_approvals": {},
        "cost_total_usd": 0.0,
        "errors": [],
        "messages": [],
    }

    try:
        pipeline = _get_pipeline()
        config = {"configurable": {"thread_id": run_id}}
        pipeline.invoke(initial_state, config)

        _flush()
        state = pipeline.get_state(config)
        summary = _state_summary(state)
        summary["action"] = "run_started"
        return json.dumps(summary, indent=2)

    except Exception as exc:
        _flush()
        return json.dumps({"error": str(exc), "run_id": run_id})


@mcp.tool()
def pineapple_status() -> str:
    """List active/recent pipeline runs from the checkpoint database.

    Returns:
        JSON with a list of recent run IDs.
    """
    db_path = os.path.abspath(DEFAULT_DB_PATH)
    if not os.path.exists(db_path):
        return json.dumps({"runs": [], "message": "No checkpoint database found."})

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.execute(
            "SELECT DISTINCT thread_id FROM checkpoints ORDER BY rowid DESC LIMIT 20"
        )
        run_ids = [row[0] for row in cursor.fetchall()]
        conn.close()
    except Exception as exc:
        return json.dumps({"error": f"Could not read checkpoint database: {exc}"})

    if not run_ids:
        return json.dumps({"runs": [], "message": "No pipeline runs found."})

    # Get state summary for each run
    results = []
    try:
        pipeline = _get_pipeline()
        for rid in run_ids:
            config = {"configurable": {"thread_id": rid}}
            state = pipeline.get_state(config)
            results.append(_state_summary(state))
    except Exception:
        # Fall back to just listing IDs
        results = [{"run_id": rid} for rid in run_ids]

    return json.dumps({"runs": results}, indent=2)


@mcp.tool()
def pineapple_approve(run_id: str) -> str:
    """Approve the current gate and continue the pipeline to the next gate or completion.

    Args:
        run_id: The pipeline run ID to approve.

    Returns:
        JSON with the updated state after approval.
    """
    try:
        pipeline = _get_pipeline()
        config = {"configurable": {"thread_id": run_id}}

        state = pipeline.get_state(config)
        if state.values is None or not state.values:
            return json.dumps({"error": f"No checkpoint found for run: {run_id}"})

        if not state.next:
            return json.dumps({
                "error": "Run already complete, nothing to approve.",
                "run_id": run_id,
                "current_stage": state.values.get("current_stage", ""),
            })

        next_node = state.next[0]
        approvals = state.values.get("human_approvals", {})

        # Record approval and resume
        pipeline.update_state(
            config,
            {"human_approvals": {**approvals, next_node: True}},
        )
        pipeline.invoke(None, config)

        _flush()
        # Get updated state
        new_state = pipeline.get_state(config)
        summary = _state_summary(new_state)
        summary["action"] = "approved"
        summary["approved_gate"] = next_node
        return json.dumps(summary, indent=2)

    except Exception as exc:
        _flush()
        return json.dumps({"error": str(exc), "run_id": run_id})


@mcp.tool()
def pineapple_get_state(run_id: str) -> str:
    """Get the current state of a pipeline run.

    Args:
        run_id: The pipeline run ID to inspect.

    Returns:
        JSON with the full state summary.
    """
    try:
        pipeline = _get_pipeline()
        config = {"configurable": {"thread_id": run_id}}

        state = pipeline.get_state(config)
        if state.values is None or not state.values:
            return json.dumps({"error": f"No checkpoint found for run: {run_id}"})

        summary = _state_summary(state)

        # Include artifact availability
        vals = state.values
        summary["artifacts"] = {
            "context_bundle": vals.get("context_bundle") is not None,
            "strategic_brief": vals.get("strategic_brief") is not None,
            "design_spec": vals.get("design_spec") is not None,
            "task_plan": vals.get("task_plan") is not None,
            "workspace_info": vals.get("workspace_info") is not None,
            "build_results": len(vals.get("build_results", [])),
            "verify_record": vals.get("verify_record") is not None,
            "review_result": vals.get("review_result") is not None,
            "ship_result": vals.get("ship_result") is not None,
            "evolve_report": vals.get("evolve_report") is not None,
        }

        # Include errors if any
        errors = vals.get("errors", [])
        if errors:
            summary["errors"] = errors

        return json.dumps(summary, indent=2)

    except Exception as exc:
        return json.dumps({"error": str(exc), "run_id": run_id})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
