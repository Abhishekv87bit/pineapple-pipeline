"""Test human-in-the-loop interrupt gates in the Pineapple Pipeline.

Tests:
1. Full path: interrupts fire at strategic_review, architecture, plan, ship
2. Lightweight path: only ship interrupt fires (skips to build directly)
3. Medium path: plan and ship interrupts fire
4. Resume: can pick up where left off after quitting
"""
import os
import sys

# Ensure src is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from pineapple.graph import create_pipeline, INTERRUPT_NODES
from pineapple.state import PipelineStage

# Use a fixed test directory instead of tempdir to avoid Windows cleanup issues
TEST_DIR = os.path.join(os.path.dirname(__file__), ".pineapple_test")
os.makedirs(TEST_DIR, exist_ok=True)

_test_counter = 0


def _db_path():
    global _test_counter
    _test_counter += 1
    return os.path.join(TEST_DIR, f"test_{_test_counter}.db")


def make_initial_state(path: str = "full", run_id: str = "test-001") -> dict:
    return {
        "run_id": run_id,
        "request": "Build test project",
        "project_name": "TestProject",
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


def test_full_path():
    """Full path should pause at strategic_review, architecture, plan, ship."""
    print("=" * 60)
    print("TEST: Full path interrupt gates")
    print("=" * 60)

    db = _db_path()
    pipeline = create_pipeline(db_path=db)
    config = {"configurable": {"thread_id": "full-test"}}

    # First invoke -- should run intake then pause before strategic_review
    pipeline.invoke(make_initial_state("full", "full-test"), config)
    state = pipeline.get_state(config)
    assert state.next, "Expected interrupt but graph finished"
    assert state.next[0] == "strategic_review", f"Expected strategic_review, got {state.next[0]}"
    print(f"  PASS: Paused at {state.next[0]}")

    # Approve strategic_review and resume
    approvals = state.values.get("human_approvals", {})
    pipeline.update_state(config, {"human_approvals": {**approvals, "strategic_review": True}})
    pipeline.invoke(None, config)
    state = pipeline.get_state(config)
    assert state.next and state.next[0] == "architecture", f"Expected architecture, got {state.next}"
    print(f"  PASS: Paused at {state.next[0]}")

    # Approve architecture and resume
    approvals = state.values.get("human_approvals", {})
    pipeline.update_state(config, {"human_approvals": {**approvals, "architecture": True}})
    pipeline.invoke(None, config)
    state = pipeline.get_state(config)
    assert state.next and state.next[0] == "plan", f"Expected plan, got {state.next}"
    print(f"  PASS: Paused at {state.next[0]}")

    # Approve plan and resume
    approvals = state.values.get("human_approvals", {})
    pipeline.update_state(config, {"human_approvals": {**approvals, "plan": True}})
    pipeline.invoke(None, config)
    state = pipeline.get_state(config)
    assert state.next and state.next[0] == "ship", f"Expected ship, got {state.next}"
    print(f"  PASS: Paused at {state.next[0]}")

    # Approve ship and resume -- should complete
    approvals = state.values.get("human_approvals", {})
    pipeline.update_state(config, {"human_approvals": {**approvals, "ship": True}})
    pipeline.invoke(None, config)
    state = pipeline.get_state(config)
    assert not state.next, f"Expected completion, but next={state.next}"
    print(f"  PASS: Pipeline completed")

    print("  ALL PASSED\n")


def test_lightweight_path():
    """Lightweight path skips to build -- only ship interrupt fires."""
    print("=" * 60)
    print("TEST: Lightweight path (ship interrupt only)")
    print("=" * 60)

    db = _db_path()
    pipeline = create_pipeline(db_path=db)
    config = {"configurable": {"thread_id": "lightweight-test"}}

    pipeline.invoke(make_initial_state("lightweight", "lightweight-test"), config)
    state = pipeline.get_state(config)

    # Lightweight goes: intake -> build -> verify -> review -> ship (interrupt!)
    assert state.next and state.next[0] == "ship", f"Expected ship interrupt, got {state.next}"
    print(f"  PASS: Only paused at ship (strategic_review/architecture/plan skipped)")

    # Approve ship to complete
    approvals = state.values.get("human_approvals", {})
    pipeline.update_state(config, {"human_approvals": {**approvals, "ship": True}})
    pipeline.invoke(None, config)
    state = pipeline.get_state(config)
    assert not state.next, f"Expected completion, but next={state.next}"
    print(f"  PASS: Pipeline completed after ship approval")

    print("  ALL PASSED\n")


def test_medium_path():
    """Medium path skips strategic_review and architecture, goes to plan."""
    print("=" * 60)
    print("TEST: Medium path (plan + ship interrupts only)")
    print("=" * 60)

    db = _db_path()
    pipeline = create_pipeline(db_path=db)
    config = {"configurable": {"thread_id": "medium-test"}}

    pipeline.invoke(make_initial_state("medium", "medium-test"), config)
    state = pipeline.get_state(config)
    assert state.next and state.next[0] == "plan", f"Expected plan, got {state.next}"
    print(f"  PASS: Paused at {state.next[0]} (strategic_review/architecture skipped)")

    # Approve plan
    approvals = state.values.get("human_approvals", {})
    pipeline.update_state(config, {"human_approvals": {**approvals, "plan": True}})
    pipeline.invoke(None, config)
    state = pipeline.get_state(config)
    assert state.next and state.next[0] == "ship", f"Expected ship, got {state.next}"
    print(f"  PASS: Paused at {state.next[0]}")

    # Approve ship
    approvals = state.values.get("human_approvals", {})
    pipeline.update_state(config, {"human_approvals": {**approvals, "ship": True}})
    pipeline.invoke(None, config)
    state = pipeline.get_state(config)
    assert not state.next, f"Expected completion, but next={state.next}"
    print(f"  PASS: Pipeline completed")

    print("  ALL PASSED\n")


def test_resume():
    """Test that a paused run can be resumed from checkpoint."""
    print("=" * 60)
    print("TEST: Resume from checkpoint")
    print("=" * 60)

    db = _db_path()
    thread_id = "resume-test"
    config = {"configurable": {"thread_id": thread_id}}

    # Start a run, let it pause at strategic_review
    pipeline1 = create_pipeline(db_path=db)
    pipeline1.invoke(make_initial_state("full", thread_id), config)
    state = pipeline1.get_state(config)
    assert state.next and state.next[0] == "strategic_review"
    print(f"  PASS: First run paused at {state.next[0]}")

    # Simulate "quitting" by creating a NEW pipeline from same db
    pipeline2 = create_pipeline(db_path=db)
    state = pipeline2.get_state(config)
    assert state.next and state.next[0] == "strategic_review"
    print(f"  PASS: Resumed pipeline sees pause at {state.next[0]}")

    # Approve and continue
    approvals = state.values.get("human_approvals", {})
    pipeline2.update_state(config, {"human_approvals": {**approvals, "strategic_review": True}})
    pipeline2.invoke(None, config)
    state = pipeline2.get_state(config)
    assert state.next and state.next[0] == "architecture"
    print(f"  PASS: After approval, advanced to {state.next[0]}")

    print("  ALL PASSED\n")


if __name__ == "__main__":
    test_full_path()
    test_lightweight_path()
    test_medium_path()
    test_resume()
    print("=" * 60)
    print("ALL TESTS PASSED")
    print("=" * 60)
