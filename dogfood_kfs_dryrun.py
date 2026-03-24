"""Dogfood dry-run v2: Pipeline stages 0-3 on KFS Manifest System.
Runs Intake -> Strategic Review -> Architecture -> Plan.
Does NOT build code. Observation mode only.

v2 changes: Uses target_dir so intake scans KFS repo, not pipeline repo.
            Shorter request -- spec content comes from codebase scan.
"""
import sys, uuid, json
from pathlib import Path

sys.path.insert(0, "src")

from pineapple.state import PipelineState
from pineapple.agents.intake import intake_node
from pineapple.agents.strategic_review import strategic_review_node
from pineapple.agents.architecture import architecture_node
from pineapple.agents.planner import plan_node

request = "Implement the KFS Manifest System — a unified .kfs.yaml format bridging geometry generation and motion simulation for Kinetic Forge Studio"

run_id = str(uuid.uuid4())
state = {
    "run_id": run_id,
    "request": request,
    "project_name": "kfs-manifest-system",
    "target_dir": r"d:\Claude local\kinetic-forge-studio",
    "branch": "main",
    "path": "full",
    "current_stage": "intake",
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
    "human_approvals": {"strategic_review": True, "architecture": True, "plan": True, "ship": True},
    "cost_total_usd": 0.0,
    "errors": [],
    "messages": [],
}

print(f"\n{'='*60}")
print(f"DOGFOOD DRY-RUN: KFS Manifest System")
print(f"Run ID: {run_id}")
print(f"Path: full (stages 0-3 only)")
print(f"{'='*60}\n")

# Stage 0: Intake
print(">>> STAGE 0: INTAKE")
result = intake_node(state)
state.update(result)
print(f"    Path: {state.get('path')}")
print(f"    Project: {state.get('project_name')}")
print(f"    Context bundle: {'YES' if state.get('context_bundle') else 'NO'}")
bundle = state.get("context_bundle", {})
if bundle:
    cs = bundle.get("codebase_summary", {})
    print(f"    Tech stack: {cs.get('tech_stack', [])}")
    print(f"    Directories: {cs.get('directories', [])[:10]}")
    print(f"    File counts (top 5): {dict(list(cs.get('file_counts', {}).items())[:5])}")
    pm = bundle.get("project_memory", {})
    print(f"    Memory sources: {pm.get('memory_sources', [])}")
    print(f"    Locked decisions: {len(pm.get('locked_decisions', []))} entries")
    for i, ld in enumerate(pm.get("locked_decisions", [])[:3]):
        print(f"      Decision {i+1}: {ld[:120]}...")

# Stage 1: Strategic Review
print("\n>>> STAGE 1: STRATEGIC REVIEW")
result = strategic_review_node(state)
state.update(result)
brief = state.get("strategic_brief", {})
print(f"    What: {brief.get('what', 'N/A')[:200]}")
print(f"    Why: {brief.get('why', 'N/A')[:200]}")
print(f"    Not building: {brief.get('not_building', [])}")
print(f"    Approved: {brief.get('approved', False)}")

# Stage 2: Architecture
print("\n>>> STAGE 2: ARCHITECTURE")
result = architecture_node(state)
state.update(result)
spec = state.get("design_spec", {})
print(f"    Summary: {spec.get('summary', 'N/A')[:200]}")
components = spec.get("components", [])
print(f"    Components: {len(components)}")
for c in components[:5]:
    print(f"      - {c.get('name', '?')}: {c.get('description', '?')[:80]}")
tech_choices = spec.get("technology_choices", {})
print(f"    Technology choices: {len(tech_choices)} entries")
for k, v in list(tech_choices.items())[:5]:
    print(f"      {k}: {v}")

# Stage 3: Plan
print("\n>>> STAGE 3: PLAN")
result = plan_node(state)
state.update(result)
plan = state.get("task_plan", {})
tasks = plan.get("tasks", [])
print(f"    Tasks: {len(tasks)}")
print(f"    Total estimated cost: ${plan.get('total_estimated_cost_usd', 0):.2f}")
for t in tasks:
    print(f"      [{t.get('id')}] {t.get('description', '?')[:80]}")
    print(f"          Files: {t.get('files_to_create', [])[:3]}")
    print(f"          Complexity: {t.get('complexity', '?')}")

# Save full state
output_path = Path(f".pineapple/dogfood/kfs-manifest-dryrun-{run_id[:8]}.json")
output_path.parent.mkdir(parents=True, exist_ok=True)
output_path.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
print(f"\n>>> Full state saved to: {output_path}")

print(f"\n{'='*60}")
print(f"DRY-RUN COMPLETE -- Stages 0-3 finished")
print(f"Cost: ${state.get('cost_total_usd', 0):.4f}")
print(f"Errors: {len(state.get('errors', []))}")
print(f"{'='*60}")
