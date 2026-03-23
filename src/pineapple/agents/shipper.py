"""Stage 8: Shipper — determine and execute shipping action.

Pure Python — no LLM calls. Handles: merge, pr, keep, discard.
"""

import shutil
import subprocess

from pineapple.models import ShipResult
from pineapple.state import PipelineState


def _run_git(args: list[str], cwd: str = ".") -> tuple[int, str, str]:
    """Run a git command and return (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=30,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return 1, "", str(exc)


def _gh_available() -> bool:
    """Check if the GitHub CLI (gh) is available."""
    return shutil.which("gh") is not None


def _determine_action(state: PipelineState) -> str:
    """Determine the shipping action based on review result and path.

    Returns one of: "merge", "pr", "keep", "discard".
    """
    review_result = state.get("review_result")
    path = state.get("path", "full")

    # If no review result, keep by default
    if not review_result:
        return "keep"

    verdict = review_result.get("verdict", "unknown")

    # If review didn't pass, keep with documented issues
    if verdict != "pass":
        return "keep"

    # Review passed — decide based on path
    if path == "lightweight":
        return "keep"
    else:
        # full or medium path: default to PR
        return "pr"


def _do_pr(state: PipelineState) -> ShipResult:
    """Create a pull request via gh CLI."""
    project_name = state.get("project_name", "unknown")
    request = state.get("request", "")
    branch = state.get("branch", "")

    # Build PR title
    title = f"[{project_name}] {request}"
    if len(title) > 72:
        title = title[:69] + "..."

    # Build PR body
    body_parts = [f"## Ship Summary\n\n**Project:** {project_name}"]

    build_results = state.get("build_results", [])
    completed = sum(1 for r in build_results if r.get("status") == "completed")
    failed = sum(1 for r in build_results if r.get("status") == "failed")
    body_parts.append(
        f"**Build:** {completed} completed, {failed} failed out of {len(build_results)} tasks"
    )

    verify_record = state.get("verify_record")
    if verify_record:
        all_green = verify_record.get("all_green", False)
        layers = verify_record.get("layers", [])
        body_parts.append(
            f"**Verify:** {'ALL GREEN' if all_green else 'ISSUES'} ({len(layers)} layers)"
        )

    review_result = state.get("review_result")
    if review_result:
        verdict = review_result.get("verdict", "unknown")
        body_parts.append(f"**Review:** {verdict}")
        critical = review_result.get("critical_issues", [])
        if critical:
            body_parts.append(
                "\n### Critical Issues\n" + "\n".join(f"- {i}" for i in critical)
            )
        important = review_result.get("important_issues", [])
        if important:
            body_parts.append(
                "\n### Important Issues\n" + "\n".join(f"- {i}" for i in important)
            )

    cost = state.get("cost_total_usd", 0.0)
    body_parts.append(f"\n**Cost:** ${cost:.4f}")
    body_parts.append("\n---\n*Created by Pineapple Pipeline Stage 8: Ship*")

    body = "\n".join(body_parts)

    # Check gh availability
    if not _gh_available():
        print("  [Ship] gh CLI not available — falling back to 'keep'")
        print(f"  [Ship] To create PR manually: gh pr create --title \"{title}\"")
        return ShipResult(action="keep", pr_url=None, merge_commit=None)

    # Check we're on a branch (not main/master)
    rc, current_branch, _ = _run_git(["rev-parse", "--abbrev-ref", "HEAD"])
    if rc != 0:
        print("  [Ship] Not in a git repo — falling back to 'keep'")
        return ShipResult(action="keep", pr_url=None, merge_commit=None)

    if current_branch in ("main", "master"):
        print("  [Ship] On main/master — cannot create PR from default branch")
        print("  [Ship] Falling back to 'keep'")
        return ShipResult(action="keep", pr_url=None, merge_commit=None)

    # Push current branch to remote
    rc, out, err = _run_git(["push", "-u", "origin", current_branch])
    if rc != 0:
        print(f"  [Ship] Push failed: {err}")
        print("  [Ship] Falling back to 'keep'")
        return ShipResult(action="keep", pr_url=None, merge_commit=None)

    # Create PR
    try:
        result = subprocess.run(
            ["gh", "pr", "create", "--title", title, "--body", body],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            pr_url = result.stdout.strip()
            print(f"  [Ship] PR created: {pr_url}")
            return ShipResult(action="pr", pr_url=pr_url, merge_commit=None)
        else:
            print(f"  [Ship] gh pr create failed: {result.stderr.strip()}")
            print("  [Ship] Falling back to 'keep'")
            return ShipResult(action="keep", pr_url=None, merge_commit=None)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        print(f"  [Ship] gh pr create error: {exc}")
        print("  [Ship] Falling back to 'keep'")
        return ShipResult(action="keep", pr_url=None, merge_commit=None)


def _do_merge(state: PipelineState) -> ShipResult:
    """Merge feature branch into main via --no-ff."""
    review_result = state.get("review_result")
    verify_record = state.get("verify_record")

    # Safety: only merge if review passed AND verification is all green
    if not review_result or review_result.get("verdict") != "pass":
        print("  [Ship] Review did not pass — refusing to merge")
        return ShipResult(action="keep", pr_url=None, merge_commit=None)

    if not verify_record or not verify_record.get("all_green", False):
        print("  [Ship] Verification not all green — refusing to merge")
        return ShipResult(action="keep", pr_url=None, merge_commit=None)

    # Get current branch
    rc, current_branch, _ = _run_git(["rev-parse", "--abbrev-ref", "HEAD"])
    if rc != 0 or current_branch in ("main", "master"):
        print("  [Ship] Cannot merge — not on a feature branch")
        return ShipResult(action="keep", pr_url=None, merge_commit=None)

    # Determine target branch (main or master)
    rc, _, _ = _run_git(["rev-parse", "--verify", "refs/heads/main"])
    target = "main" if rc == 0 else "master"

    # Switch to target
    rc, _, err = _run_git(["checkout", target])
    if rc != 0:
        print(f"  [Ship] Cannot checkout {target}: {err}")
        # Switch back
        _run_git(["checkout", current_branch])
        return ShipResult(action="keep", pr_url=None, merge_commit=None)

    # Merge with --no-ff
    project_name = state.get("project_name", "unknown")
    merge_msg = f"Merge branch '{current_branch}' — {project_name}"
    rc, out, err = _run_git(["merge", "--no-ff", current_branch, "-m", merge_msg])
    if rc != 0:
        print(f"  [Ship] Merge failed: {err}")
        # Abort merge if in progress, switch back
        _run_git(["merge", "--abort"])
        _run_git(["checkout", current_branch])
        return ShipResult(action="keep", pr_url=None, merge_commit=None)

    # Get merge commit hash
    rc, merge_hash, _ = _run_git(["rev-parse", "HEAD"])
    if rc != 0:
        merge_hash = "unknown"

    print(f"  [Ship] Merged into {target}: {merge_hash}")
    return ShipResult(action="merge", pr_url=None, merge_commit=merge_hash)


def _do_keep(state: PipelineState) -> ShipResult:
    """Keep code on branch — no git operations."""
    branch = state.get("branch", "current branch")
    print(f"  [Ship] Code stays on branch: {branch}")
    print("  [Ship] No git operations performed")
    return ShipResult(action="keep", pr_url=None, merge_commit=None)


def _do_discard(state: PipelineState) -> ShipResult:
    """Discard work: remove worktree and delete branch."""
    branch = state.get("branch", "")
    workspace_info = state.get("workspace_info")

    # Remove worktree if one exists
    if workspace_info:
        worktree_path = workspace_info.get("worktree_path")
        if worktree_path:
            rc, _, err = _run_git(["worktree", "remove", worktree_path])
            if rc == 0:
                print(f"  [Ship] Removed worktree: {worktree_path}")
            else:
                print(f"  [Ship] Could not remove worktree: {err}")

    # Check current branch before deleting
    rc, current_branch, _ = _run_git(["rev-parse", "--abbrev-ref", "HEAD"])
    if rc == 0 and branch and branch == current_branch:
        # Switch off the branch before deleting it
        rc_main, _, _ = _run_git(["rev-parse", "--verify", "refs/heads/main"])
        target = "main" if rc_main == 0 else "master"
        _run_git(["checkout", target])

    # Delete feature branch (safe delete, not force)
    if branch and branch not in ("main", "master"):
        rc, _, err = _run_git(["branch", "-d", branch])
        if rc == 0:
            print(f"  [Ship] Deleted branch: {branch}")
        else:
            print(f"  [Ship] Could not delete branch: {err}")
            print("  [Ship] Branch may have unmerged changes — use 'git branch -D' manually if certain")
    else:
        print("  [Ship] No feature branch to delete")

    return ShipResult(action="discard", pr_url=None, merge_commit=None)


def ship_node(state: PipelineState) -> dict:
    """Print a summary of what was built, verified, and reviewed, then execute shipping action.

    Determines action based on review result and path:
    - review pass + lightweight path -> keep
    - review pass + full/medium path -> pr
    - review not pass -> keep with documented issues
    - circuit breaker hit -> keep
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

    # Determine action
    action = _determine_action(state)
    print(f"  [Ship] Determined action: {action}")

    # Execute action
    if action == "pr":
        result = _do_pr(state)
    elif action == "merge":
        result = _do_merge(state)
    elif action == "discard":
        result = _do_discard(state)
    else:
        result = _do_keep(state)

    # Print final shipping summary
    print(f"\n  [Ship] === SHIPPING SUMMARY ===")
    print(f"  [Ship] Action taken: {result.action}")
    if result.pr_url:
        print(f"  [Ship] PR URL: {result.pr_url}")
    if result.merge_commit:
        print(f"  [Ship] Merge commit: {result.merge_commit}")

    reason = _action_reason(state, result.action)
    print(f"  [Ship] Reason: {reason}")
    print(f"  [Ship] ========================\n")

    return {
        "current_stage": "ship",
        "ship_result": result.model_dump(),
    }


def _action_reason(state: PipelineState, action: str) -> str:
    """Generate a human-readable reason for the shipping action."""
    review_result = state.get("review_result")
    path = state.get("path", "full")
    verdict = review_result.get("verdict", "unknown") if review_result else "none"

    if action == "pr":
        return f"Review passed on {path} path — PR created for human review"
    elif action == "merge":
        return f"Review passed, all verification green — merged to main"
    elif action == "discard":
        return "Work discarded — branch and worktree cleaned up"
    elif action == "keep" and verdict == "pass" and path == "lightweight":
        return "Review passed on lightweight path — code stays on branch"
    elif action == "keep" and verdict != "pass":
        return f"Review verdict '{verdict}' — code kept on branch with documented issues"
    else:
        return "Code stays on branch — no git operations performed"
