"""Full dogfood: Pipeline stages 5-9 on KFS Manifest System.
Builds REAL code in the KFS repo.
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

# Load dry-run state
state_path = Path(".pineapple/dogfood/kfs-manifest-dryrun-7dfa5eae.json")
state = json.loads(state_path.read_text(encoding="utf-8"))
print(f"Loaded state from dry-run: {state['run_id'][:8]}")
print(f"Task plan: {len(state.get('task_plan', {}).get('tasks', []))} tasks")

# Point workspace at KFS repo
kfs_dir = r"d:\Claude local\kinetic-forge-studio"
state["target_dir"] = kfs_dir
state["workspace_info"] = {
    "worktree_path": kfs_dir,
    "branch": "feat/kfs-manifest-system-pipeline",
    "run_dir": str(Path(kfs_dir) / ".pineapple" / "runs" / state["run_id"][:8]),
    "tools_available": {"python": True, "git": True, "pytest": True},
    "scaffolded_files": []
}
state["human_approvals"] = {
    "strategic_review": True, "architecture": True,
    "plan": True, "ship": True
}

# Limit to first 6 tasks (3 impl + 3 tests) to keep manageable
original_tasks = state["task_plan"]["tasks"]
state["task_plan"]["tasks"] = original_tasks[:6]
print(f"Running first 6 tasks (of {len(original_tasks)}):")
for t in state["task_plan"]["tasks"]:
    print(f"  [{t['id']}] {t['description'][:70]}")

start = time.time()
print(f"\n{'='*60}")
print(f"FULL DOGFOOD: KFS Manifest System")
print(f"Target: {kfs_dir}")
print(f"Branch: feat/kfs-manifest-system-pipeline")
print(f"{'='*60}\n")

# Stage 4: Setup (create run dir)
print(">>> STAGE 4: SETUP")
result = setup_node(state)
state.update(result)
ws = state.get("workspace_info", {})
print(f"    Run dir: {ws.get('run_dir', 'N/A')}")
print(f"    Tools: {ws.get('tools_available', {})}")

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

# Stage 6: Verify
print("\n>>> STAGE 6: VERIFY")
result = verifier_node(state)
state.update(result)
record = state.get("verify_record", {})
print(f"    All green: {record.get('all_green', False)}")
layers = record.get("layers", [])
for layer in layers:
    print(f"    {layer.get('name', '?')}: {layer.get('status', '?')}")

# Stage 7: Review
print("\n>>> STAGE 7: REVIEW")
result = reviewer_node(state)
state.update(result)
review = state.get("review_result", {})
print(f"    Verdict: {review.get('verdict', 'N/A')}")
print(f"    Critical: {len(review.get('critical_issues', []))}")
print(f"    Important: {len(review.get('important_issues', []))}")
print(f"    Minor: {len(review.get('minor_issues', []))}")

# Stage 8: Ship
print("\n>>> STAGE 8: SHIP")
result = ship_node(state)
state.update(result)
ship = state.get("ship_result", {})
print(f"    Action: {ship.get('action', 'N/A')}")
print(f"    PR URL: {ship.get('pr_url', 'N/A')}")

# Stage 9: Evolve
print("\n>>> STAGE 9: EVOLVE")
result = evolve_node(state)
state.update(result)
evolve = state.get("evolve_report", {})
print(f"    Decisions: {len(evolve.get('decisions_logged', []))}")

elapsed = time.time() - start
print(f"\n{'='*60}")
print(f"FULL DOGFOOD COMPLETE")
print(f"Time: {elapsed:.1f}s")
print(f"Cost: ${state.get('cost_total_usd', 0):.4f}")
print(f"Errors: {len(state.get('errors', []))}")
print(f"Files written: {files_total}")
print(f"{'='*60}")

# Save state
out = Path(f".pineapple/dogfood/kfs-manifest-full-{state['run_id'][:8]}.json")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
print(f"\nState saved to: {out}")
