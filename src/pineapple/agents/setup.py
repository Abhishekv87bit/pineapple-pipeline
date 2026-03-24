"""Stage 4: Setup — prepare workspace for building.

Creates git worktree, scaffolds project structure, prepares run directory.
Pure Python — no LLM calls. Uses superpowers:using-git-worktrees skill.
"""
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from pineapple.state import PipelineState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_git(*args: str, cwd: str = None) -> subprocess.CompletedProcess:
    """Run a git command safely, returning CompletedProcess."""
    return subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=cwd,
    )


def _is_git_repo() -> bool:
    """Check if CWD is inside a git repository."""
    try:
        result = _run_git("rev-parse", "--is-inside-work-tree")
        return result.returncode == 0 and result.stdout.strip() == "true"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _sanitize_branch_name(name: str) -> str:
    """Sanitize a string for use in a git branch name."""
    # Replace spaces and special chars with hyphens, lowercase
    sanitized = re.sub(r"[^a-zA-Z0-9_-]", "-", name.lower())
    # Collapse multiple hyphens
    sanitized = re.sub(r"-+", "-", sanitized)
    # Strip leading/trailing hyphens
    return sanitized.strip("-")


def _check_tools() -> dict:
    """Check availability of required tools. Returns {tool: bool}."""
    tools = {}
    for tool in ["python", "git", "pytest"]:
        try:
            result = subprocess.run(
                [tool, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            tools[tool] = result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            tools[tool] = False
    return tools


def _create_run_dir(run_id: str, request: str, path: str) -> str:
    """Create .pineapple/runs/<run_id>/ and write run_info.json.

    Returns the run directory path as a string.
    """
    run_dir = Path.cwd() / ".pineapple" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    run_info = {
        "run_id": run_id,
        "request": request,
        "path": path,
        "working_directory": str(Path.cwd()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    info_file = run_dir / "run_info.json"
    info_file.write_text(json.dumps(run_info, indent=2), encoding="utf-8")

    return str(run_dir)


def _setup_worktree(
    project_name: str, run_id: str
) -> tuple:
    """Create a git feature branch and worktree.

    Returns (worktree_path, branch_name) on success, or (None, None) on
    failure. Never force-pushes or deletes branches.
    """
    if not _is_git_repo():
        return None, None

    short_id = run_id[:8] if len(run_id) > 8 else run_id
    branch_name = f"feat/{_sanitize_branch_name(project_name)}-{short_id}"

    # Get the current branch to use as the base
    try:
        result = _run_git("branch", "--show-current")
        base_branch = result.stdout.strip() or "main"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None, None

    # Create feature branch (if not exists)
    result = _run_git("branch", "--list", branch_name)
    branch_exists = bool(result.stdout.strip())

    if not branch_exists:
        result = _run_git("branch", branch_name, base_branch)
        if result.returncode != 0:
            print(f"  [WARN] Failed to create branch {branch_name}: "
                  f"{result.stderr.strip()}")
            return None, None

    # Create worktree directory
    worktree_dir = Path.cwd() / ".pineapple" / "worktrees" / run_id
    worktree_dir.parent.mkdir(parents=True, exist_ok=True)

    # If worktree already exists, reuse it
    if worktree_dir.exists():
        return str(worktree_dir), branch_name

    result = _run_git("worktree", "add", str(worktree_dir), branch_name)
    if result.returncode != 0:
        print(f"  [WARN] Failed to create worktree: {result.stderr.strip()}")
        # Clean up the branch we just created if worktree failed
        # (only if we created it, not if it existed before)
        if not branch_exists:
            _run_git("branch", "-d", branch_name)
        return None, None

    return str(worktree_dir), branch_name


def _scaffold_files(task_plan: dict, worktree_path: str = None) -> list:
    """Create stub files for each planned file in the task plan.

    Returns list of created file paths (relative to workspace root).
    """
    if not task_plan:
        return []

    scaffolded = []
    base = Path(worktree_path) if worktree_path else Path.cwd()

    # task_plan may have "tasks" list, each task may have "files" list
    tasks = task_plan.get("tasks", [])
    if not tasks:
        return []

    for task in tasks:
        files = task.get("files_to_create", []) + task.get("files_to_modify", [])
        for file_path in files:
            if not file_path or not isinstance(file_path, str):
                continue

            target = base / file_path
            # Only create if it doesn't already exist
            if target.exists():
                continue

            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                # Write a minimal stub
                if file_path.endswith(".py"):
                    stub = f'"""Stub for {file_path} — generated by Pineapple Setup."""\n'
                elif file_path.endswith((".yml", ".yaml")):
                    stub = f"# Stub for {file_path} -- generated by Pineapple Setup\n"
                elif file_path.endswith(".json"):
                    stub = "{}\n"
                else:
                    stub = ""
                target.write_text(stub, encoding="utf-8")
                scaffolded.append(file_path)
            except OSError as exc:
                print(f"  [WARN] Could not scaffold {file_path}: {exc}")

    return scaffolded


# ---------------------------------------------------------------------------
# Stage node
# ---------------------------------------------------------------------------


def setup_node(state: PipelineState) -> dict:
    """Prepare workspace: check tools, create worktree, scaffold files."""
    run_id = state.get("run_id", "unknown")
    project_name = state.get("project_name", "unnamed")
    request = state.get("request", "")
    pipeline_path = state.get("path", "full")
    task_plan = state.get("task_plan")

    print(f"[STAGE 4] Setting up workspace for run {run_id}...")

    # 1. Check available tools
    tools_available = _check_tools()
    print(f"  Tools: {', '.join(k for k, v in tools_available.items() if v)}")

    # 2. Create run directory with metadata
    run_dir = _create_run_dir(run_id, request, pipeline_path)
    print(f"  Run dir: {run_dir}")

    # 3. Create git worktree (if in a git repo)
    worktree_path = None
    branch = None
    if tools_available.get("git"):
        worktree_path, branch = _setup_worktree(project_name, run_id)
        if worktree_path:
            print(f"  Worktree: {worktree_path}")
            print(f"  Branch: {branch}")
        else:
            print("  Worktree: skipped (not a git repo or creation failed)")
    else:
        print("  Worktree: skipped (git not available)")

    # 4. Scaffold stub files (only if task_plan exists — skip for lightweight)
    scaffolded_files = []
    if task_plan:
        scaffolded_files = _scaffold_files(task_plan, worktree_path)
        if scaffolded_files:
            print(f"  Scaffolded {len(scaffolded_files)} files")
    else:
        print("  Scaffolding: skipped (no task plan)")

    # Build workspace_info
    workspace_info = {
        "worktree_path": worktree_path,
        "branch": branch,
        "run_dir": run_dir,
        "tools_available": tools_available,
        "scaffolded_files": scaffolded_files,
    }

    print("[STAGE 4] Setup complete.")

    return {
        "current_stage": "setup",
        "workspace_info": workspace_info,
    }
