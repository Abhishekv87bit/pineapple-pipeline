"""Full dogfood v2: Pipeline stages 4-9 on KFS Manifest System.
Builds REAL code in the KFS repo. Workspace design fixed.
"""
import sys, json, uuid, time
from pathlib import Path

sys.path.insert(0, "src")

from pineapple.agents.setup import setup_node
from pineapple.agents.builder import builder_node
from pineapple.agents.verifier import verifier_node
from pineapple.agents.reviewer import reviewer_node
from pineapple.agents.shipper import ship_node
from pineapple.agents.evolver import evolve_node

# Load dry-run state (has task_plan from stages 0-3)
state_path = Path(".pineapple/dogfood/kfs-manifest-dryrun-7dfa5eae.json")
state = json.loads(state_path.read_text(encoding="utf-8"))

# Point at KFS repo — let setup_node handle workspace creation
kfs_dir = r"d:\Claude local\kinetic-forge-studio"
state["target_dir"] = kfs_dir
state["workspace_info"] = None  # Let setup_node create this
state["build_results"] = []
state["verify_record"] = None
state["review_result"] = None
state["ship_result"] = None
state["evolve_report"] = None
state["attempt_counts"] = {}
state["cost_total_usd"] = 0.0
state["errors"] = []
state["human_approvals"] = {
    "strategic_review": True, "architecture": True,
    "plan": True, "ship": True
}

# First 6 tasks only
original_tasks = state["task_plan"]["tasks"]
state["task_plan"]["tasks"] = original_tasks[:6]

print(f"{'='*60}")
print(f"FULL DOGFOOD v2: KFS Manifest System")
print(f"Target: {kfs_dir}")
print(f"Tasks: {len(state['task_plan']['tasks'])} (of {len(original_tasks)})")
print(f"{'='*60}\n")

for t in state["task_plan"]["tasks"]:
    print(f"  [{t['id']}] {t['description'][:70]}")

start = time.time()

# Stage 4: Setup — should create workspace IN KFS repo
print("\n>>> STAGE 4: SETUP")
result = setup_node(state)
state.update(result)
ws = state.get("workspace_info", {})
print(f"    Worktree: {ws.get('worktree_path', 'NONE')}")
print(f"    Branch: {ws.get('branch', 'NONE')}")
print(f"    Run dir: {ws.get('run_dir', 'NONE')}")
print(f"    Tools: {ws.get('tools_available', {})}")

# Verify workspace is in KFS, not pipeline repo
wt = ws.get("worktree_path", "")
if kfs_dir.replace("\\", "/").lower() in str(wt).replace("\\", "/").lower() or "kinetic-forge" in str(wt).lower():
    print(f"    *** WORKSPACE IN KFS REPO: YES ***")
else:
    print(f"    *** WARNING: workspace may not be in KFS repo: {wt} ***")

# Stage 5: Build
print("\n>>> STAGE 5: BUILD")
result = builder_node(state)
state.update(result)
builds = state.get("build_results", [])
completed = sum(1 for b in builds if b.get("status") == "completed")
files_total = sum(len(b.get("files_written", [])) for b in builds)
print(f"    Completed: {completed}/{len(builds)}")
print(f"    Files written: {files_total}")
print(f"    Cost: ${state.get('cost_total_usd', 0):.4f}")

# Check what files actually exist
workspace = ws.get("worktree_path") or kfs_dir
print(f"\n    Files on disk in workspace ({workspace}):")
import subprocess
result_ls = subprocess.run(
    ["git", "diff", "--stat", "HEAD"],
    capture_output=True, text=True, cwd=workspace, timeout=10
)
if result_ls.stdout:
    for line in result_ls.stdout.strip().split("\n")[:20]:
        print(f"      {line}")
else:
    # Check for untracked files
    result_ls = subprocess.run(
        ["git", "status", "--short"],
        capture_output=True, text=True, cwd=workspace, timeout=10
    )
    for line in result_ls.stdout.strip().split("\n")[:20]:
        print(f"      {line}")

# Stage 6: Verify
print("\n>>> STAGE 6: VERIFY")
result = verifier_node(state)
state.update(result)
record = state.get("verify_record", {})
print(f"    All green: {record.get('all_green', False)}")
for layer in record.get("layers", []):
    print(f"    {layer.get('name', '?')}: {layer.get('status', '?')}")

# Stage 7: Review
print("\n>>> STAGE 7: REVIEW")
result = reviewer_node(state)
state.update(result)
review = state.get("review_result", {})
print(f"    Verdict: {review.get('verdict', 'N/A')}")
print(f"    Critical: {len(review.get('critical_issues', []))}")
for issue in review.get("critical_issues", []):
    print(f"      - {issue[:80]}")

# Stage 8: Ship
print("\n>>> STAGE 8: SHIP")
result = ship_node(state)
state.update(result)
ship = state.get("ship_result", {})
print(f"    Action: {ship.get('action', 'N/A')}")

# Stage 9: Evolve
print("\n>>> STAGE 9: EVOLVE")
result = evolve_node(state)
state.update(result)

elapsed = time.time() - start
print(f"\n{'='*60}")
print(f"FULL DOGFOOD v2 COMPLETE")
print(f"Time: {elapsed:.1f}s")
print(f"Cost: ${state.get('cost_total_usd', 0):.4f}")
print(f"Files written: {files_total}")
print(f"Review verdict: {review.get('verdict', 'N/A')}")
print(f"Ship action: {ship.get('action', 'N/A')}")
print(f"{'='*60}")

# Save state
out = Path(f".pineapple/dogfood/kfs-manifest-full-v2-{state['run_id'][:8]}.json")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
print(f"\nState saved to: {out}")
