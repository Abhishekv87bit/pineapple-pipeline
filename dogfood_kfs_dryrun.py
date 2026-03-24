"""Dogfood dry-run: Pipeline stages 0-3 on KFS Manifest System.
Runs Intake -> Strategic Review -> Architecture -> Plan.
Does NOT build code. Observation mode only.
"""
import sys, uuid, json
from pathlib import Path

sys.path.insert(0, "src")

from pineapple.state import PipelineState
from pineapple.agents.intake import intake_node
from pineapple.agents.strategic_review import strategic_review_node
from pineapple.agents.architecture import architecture_node
from pineapple.agents.planner import plan_node

# Load KFS context
kfs_spec = ""
spec_path = Path(r"d:\Claude local\docs\superpowers\specs\2026-03-12-kfs-manifest-system-design.md")
if spec_path.exists():
    kfs_spec = spec_path.read_text(encoding="utf-8", errors="replace")[:5000]
    print(f"Loaded KFS manifest spec: {len(kfs_spec)} chars")
else:
    print(f"NOTE: Spec file not found at {spec_path}")
    print("      Using inline KFS Manifest System description instead.\n")

# Also try to load the KFS v2 design doc for richer context
kfs_arch = ""
arch_path = Path(r"d:\Claude local\docs\plans\2026-02-23-kinetic-forge-studio-design-v2.md")
if arch_path.exists():
    kfs_arch = arch_path.read_text(encoding="utf-8", errors="replace")[:3000]
    print(f"Loaded KFS v2 architecture doc: {len(kfs_arch)} chars")

# Build the request with inline spec since the file doesn't exist
spec_description = kfs_spec[:3000] if kfs_spec else """
## KFS Manifest System (.kfs.yaml)

A unified manifest format that bridges geometry generation and motion simulation
for Kinetic Forge Studio. Each kinetic sculpture project gets a single .kfs.yaml
file that describes:

1. **Component Definitions** - Each mechanical component (gears, cams, linkages,
   shafts, bearings) with parametric dimensions in millimeters, material, and
   generation method (CadQuery, OpenSCAD, STEP import).

2. **Assembly Graph** - How components connect: parent-child relationships,
   joint types (revolute, prismatic, fixed), coordinate transforms, and
   constraint definitions.

3. **Motion Profiles** - Per-joint motion definitions: angular velocity,
   phase offsets, motion curves (sinusoidal, linear, custom keyframes).
   Single motor input with transmission ratios through the assembly graph.

4. **Simulation Config** - Timestep, duration, collision detection toggles,
   gravity, friction coefficients.

5. **Export Targets** - Which formats to produce (STEP, STL, glTF for web
   preview, animation JSON for the React frontend).

The manifest is the single source of truth. The FastAPI backend reads it,
generates geometry via CadQuery/OpenSCAD, assembles components, runs motion
simulation, and serves results to the React frontend.

Tech stack: Python 3.12, FastAPI, React 19, TypeScript, CadQuery, OpenSCAD, SQLite.
"""

request = f"""Implement the KFS Manifest System as specified in the design doc.

Key requirements from spec:
{spec_description}

Additional architecture context:
{kfs_arch[:1500] if kfs_arch else 'Kinetic Forge Studio is a web app (React + FastAPI) for kinetic sculpture design.'}

This is for Kinetic Forge Studio -- a web app (React + FastAPI) for kinetic sculpture design.
Tech stack: Python 3.12, FastAPI, React 19, TypeScript, CadQuery, OpenSCAD, SQLite.
"""

run_id = str(uuid.uuid4())
state = {
    "run_id": run_id,
    "request": request,
    "project_name": "kfs-manifest-system",
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
