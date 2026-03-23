"""Pineapple Pipeline CLI entry point.

Usage:
    python -m pineapple.cli run "Build BrokerFlow"
    python -m pineapple.cli run "Fix bug" --path lightweight
    python -m pineapple.cli status
    python -m pineapple.cli resume <run-id>
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import uuid

from pineapple.state import PipelineStage, PipelineState

# Default checkpoint database path (relative to cwd)
DEFAULT_DB_PATH = ".pineapple/checkpoints.db"


# ---------------------------------------------------------------------------
# Stage labels for human-readable output
# ---------------------------------------------------------------------------
STAGE_LABELS: dict[str, str] = {
    PipelineStage.INTAKE.value: "Intake",
    PipelineStage.STRATEGIC_REVIEW.value: "Strategic Review",
    PipelineStage.ARCHITECTURE.value: "Architecture",
    PipelineStage.PLAN.value: "Plan",
    PipelineStage.SETUP.value: "Setup",
    PipelineStage.BUILD.value: "Build",
    PipelineStage.VERIFY.value: "Verify",
    PipelineStage.REVIEW.value: "Review",
    PipelineStage.SHIP.value: "Ship",
    PipelineStage.EVOLVE.value: "Evolve",
}

# Stages that produce key artifacts worth mentioning at gate prompts
ARTIFACT_KEYS: dict[str, str] = {
    "strategic_review": "context_bundle",
    "architecture": "strategic_brief",
    "plan": "design_spec",
    "ship": "review_result",
}


# ---------------------------------------------------------------------------
# Human-in-the-loop gate prompt
# ---------------------------------------------------------------------------

def _approval_loop(pipeline, config: dict, run_id: str) -> None:
    """Check for interrupt gates and prompt the user for approval.

    Loops until the graph finishes or the user quits.
    """
    while True:
        state = pipeline.get_state(config)
        if not state.next:
            # No pending nodes -- graph finished
            break

        next_node = state.next[0]
        label = STAGE_LABELS.get(next_node, next_node)
        current_stage = state.values.get("current_stage", "?")
        current_label = STAGE_LABELS.get(current_stage, current_stage)

        print()
        print(f"  [GATE] Pipeline paused before: {label}")
        print(f"  [GATE] Last completed stage:   {current_label}")

        # Show artifact produced by the last stage (if any)
        artifact_key = ARTIFACT_KEYS.get(next_node)
        if artifact_key:
            artifact = state.values.get(artifact_key)
            if artifact is not None:
                print(f"  [GATE] Artifact ready:         {artifact_key}")

        # Show existing approvals
        approvals = state.values.get("human_approvals", {})
        if approvals:
            approved = [k for k, v in approvals.items() if v]
            if approved:
                print(f"  [GATE] Already approved:       {', '.join(approved)}")

        print()

        try:
            choice = input("  [GATE] Approve and continue? [y/n/q]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            print(f"  [INFO] Run paused. Resume with:  pineapple resume {run_id}")
            return

        if choice == "y":
            # Record approval in state, then resume
            pipeline.update_state(
                config,
                {"human_approvals": {**approvals, next_node: True}},
            )
            result = pipeline.invoke(None, config)
        elif choice == "q":
            print(f"  [INFO] Run paused. Resume with:  pineapple resume {run_id}")
            return
        else:
            print("  [INFO] Feedback not yet implemented. Approve (y) or quit (q).")


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _cmd_run(args: argparse.Namespace) -> None:
    """Start a new pipeline run."""

    # Lazy import -- graph module may not exist yet during early development.
    try:
        from pineapple.graph import create_pipeline  # type: ignore[import-untyped]
    except ImportError:
        print("[ERROR] pineapple.graph module not found. Create graph.py with create_pipeline() first.")
        sys.exit(1)

    run_id = str(uuid.uuid4())
    path = args.path or "full"  # default; auto-detect logic will live in Intake later

    print(f"[INFO] Pipeline run started: {run_id}")
    print(f"[INFO] Path: {path}")
    print(f"[INFO] Request: {args.request}")
    if args.project_name:
        print(f"[INFO] Project: {args.project_name}")
    print()

    # Build the initial state dict
    initial_state: PipelineState = {
        "run_id": run_id,
        "request": args.request,
        "project_name": args.project_name or "",
        "branch": "",
        "path": path,
        "current_stage": PipelineStage.INTAKE.value,
        # Artifacts -- all empty at start
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
        # Control flow
        "attempt_counts": {},
        "human_approvals": {},
        "cost_total_usd": 0.0,
        "errors": [],
        "messages": [],
    }

    pipeline = create_pipeline(db_path=DEFAULT_DB_PATH)
    config = {"configurable": {"thread_id": run_id}}

    try:
        # First invoke -- runs until first interrupt or completion
        pipeline.invoke(initial_state, config)

        # Enter approval loop (handles all interrupt gates)
        _approval_loop(pipeline, config, run_id)

    except KeyboardInterrupt:
        print()
        print(f"[WARN] Run interrupted.")
        print(f"[WARN] Resume with:  pineapple resume {run_id}")
        sys.exit(130)

    # Check final state
    state = pipeline.get_state(config)
    if not state.next:
        print()
        print(f"[DONE] Pipeline run complete: {run_id}")


def _cmd_status(_args: argparse.Namespace) -> None:
    """List active/recent pipeline runs from the checkpoint store."""
    db_path = os.path.abspath(DEFAULT_DB_PATH)
    if not os.path.exists(db_path):
        print("[INFO] No checkpoint database found.")
        print(f"[INFO] Expected at: {db_path}")
        return

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.execute(
            "SELECT DISTINCT thread_id FROM checkpoints ORDER BY rowid DESC LIMIT 20"
        )
        rows = cursor.fetchall()
        conn.close()
    except Exception as exc:
        print(f"[ERROR] Could not read checkpoint database: {exc}")
        return

    if not rows:
        print("[INFO] No pipeline runs found in checkpoint store.")
        return

    print(f"[INFO] Recent pipeline runs ({len(rows)}):")
    for (thread_id,) in rows:
        print(f"  - {thread_id}")
    print()
    print("[INFO] Use 'pineapple resume <run-id>' to resume a paused run.")


def _cmd_resume(args: argparse.Namespace) -> None:
    """Resume an interrupted run from its last checkpoint."""
    try:
        from pineapple.graph import create_pipeline  # type: ignore[import-untyped]
    except ImportError:
        print("[ERROR] pineapple.graph module not found.")
        sys.exit(1)

    run_id = args.run_id
    db_path = os.path.abspath(DEFAULT_DB_PATH)

    if not os.path.exists(db_path):
        print(f"[ERROR] No checkpoint database found at: {db_path}")
        sys.exit(1)

    pipeline = create_pipeline(db_path=db_path)
    config = {"configurable": {"thread_id": run_id}}

    # Check current state of the run
    state = pipeline.get_state(config)
    if state.values is None or not state.values:
        print(f"[ERROR] No checkpoint found for run: {run_id}")
        sys.exit(1)

    if not state.next:
        print(f"[INFO] Run already complete: {run_id}")
        current = state.values.get("current_stage", "?")
        print(f"[INFO] Last stage: {STAGE_LABELS.get(current, current)}")
        return

    next_node = state.next[0]
    label = STAGE_LABELS.get(next_node, next_node)
    print(f"[INFO] Resuming run: {run_id}")
    print(f"[INFO] Paused before: {label}")
    print()

    try:
        _approval_loop(pipeline, config, run_id)
    except KeyboardInterrupt:
        print()
        print(f"[WARN] Run interrupted.")
        print(f"[WARN] Resume with:  pineapple resume {run_id}")
        sys.exit(130)

    # Check final state
    state = pipeline.get_state(config)
    if not state.next:
        print()
        print(f"[DONE] Pipeline run complete: {run_id}")


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pineapple",
        description="Pineapple Pipeline v2 -- agentic development pipeline CLI.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- run ---
    run_parser = subparsers.add_parser("run", help="Start a new pipeline run")
    run_parser.add_argument(
        "request",
        help="Description of what to build/fix (e.g. 'Build BrokerFlow')",
    )
    run_parser.add_argument(
        "--path",
        choices=["full", "medium", "lightweight"],
        default=None,
        help="Force a specific pipeline path (default: auto-detect)",
    )
    run_parser.add_argument(
        "--project-name",
        default=None,
        help="Project name (default: inferred from request)",
    )
    run_parser.set_defaults(func=_cmd_run)

    # --- status ---
    status_parser = subparsers.add_parser("status", help="List active/recent runs")
    status_parser.set_defaults(func=_cmd_status)

    # --- resume ---
    resume_parser = subparsers.add_parser("resume", help="Resume an interrupted run")
    resume_parser.add_argument("run_id", help="The run ID to resume")
    resume_parser.set_defaults(func=_cmd_resume)

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Parse arguments and dispatch to the appropriate command handler."""
    parser = _build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
