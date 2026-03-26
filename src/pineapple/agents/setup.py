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


def _is_git_repo(cwd: str = None) -> bool:
    """Check if a directory is inside a git repository."""
    try:
        result = _run_git("rev-parse", "--is-inside-work-tree", cwd=cwd)
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


def _create_run_dir(run_id: str, request: str, path: str, base_dir: str = None) -> str:
    """Create .pineapple/runs/<run_id>/ and write run_info.json.

    Args:
        base_dir: Directory under which to create .pineapple/runs/.
                  Defaults to CWD if not provided.

    Returns the run directory path as a string.
    """
    base = Path(base_dir) if base_dir else Path.cwd()
    run_dir = base / ".pineapple" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    run_info = {
        "run_id": run_id,
        "request": request,
        "path": path,
        "working_directory": str(base),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    info_file = run_dir / "run_info.json"
    info_file.write_text(json.dumps(run_info, indent=2), encoding="utf-8")

    return str(run_dir)


def _setup_worktree(
    project_name: str, run_id: str, repo_dir: str = None
) -> tuple:
    """Create a git feature branch and worktree.

    Args:
        repo_dir: The git repository root to operate on.
                  Defaults to CWD if not provided.

    Returns (worktree_path, branch_name) on success, or (None, None) on
    failure. Never force-pushes or deletes branches.
    """
    effective_dir = repo_dir or str(Path.cwd())

    if not _is_git_repo(cwd=effective_dir):
        return None, None

    short_id = run_id[:8] if len(run_id) > 8 else run_id
    branch_name = f"feat/{_sanitize_branch_name(project_name)}-{short_id}"

    # Get the current branch to use as the base
    try:
        result = _run_git("branch", "--show-current", cwd=effective_dir)
        base_branch = result.stdout.strip() or "main"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None, None

    # Create feature branch (if not exists)
    result = _run_git("branch", "--list", branch_name, cwd=effective_dir)
    branch_exists = bool(result.stdout.strip())

    if not branch_exists:
        result = _run_git("branch", branch_name, base_branch, cwd=effective_dir)
        if result.returncode != 0:
            print(f"  [WARN] Failed to create branch {branch_name}: "
                  f"{result.stderr.strip()}")
            return None, None

    # Create worktree directory under the repo, not CWD
    worktree_dir = Path(effective_dir) / ".pineapple" / "worktrees" / run_id
    worktree_dir.parent.mkdir(parents=True, exist_ok=True)

    # If worktree already exists, reuse it
    if worktree_dir.exists():
        return str(worktree_dir), branch_name

    # Enable long paths on Windows to avoid "Filename too long" errors
    _run_git("config", "core.longpaths", "true", cwd=effective_dir)

    # Use --no-checkout + sparse checkout to avoid populating files we don't need
    result = _run_git("worktree", "add", "--no-checkout", str(worktree_dir), branch_name, cwd=effective_dir)
    if result.returncode == 0:
        # Configure sparse checkout for only the directories we need
        _run_git("sparse-checkout", "init", "--cone", cwd=str(worktree_dir))
        _run_git("sparse-checkout", "set", "backend", "frontend", "src", "tests", "docs", cwd=str(worktree_dir))
        _run_git("checkout", cwd=str(worktree_dir))

    if result.returncode != 0:
        print(f"  [WARN] Failed to create worktree: {result.stderr.strip()}")
        # Clean up the branch we just created if worktree failed
        # (only if we created it, not if it existed before)
        if not branch_exists:
            _run_git("branch", "-d", branch_name, cwd=effective_dir)
        return None, None

    return str(worktree_dir), branch_name


def _install_deps(workspace: str) -> None:
    """Install dependencies in the workspace if requirements files exist."""
    ws = Path(workspace)
    installed = False

    # Try pyproject.toml first (editable install)
    if (ws / "pyproject.toml").exists():
        print("  [Setup] Installing from pyproject.toml...")
        result = subprocess.run(
            ["pip", "install", "-e", ".", "--quiet"],
            capture_output=True, text=True, timeout=120, cwd=workspace,
        )
        if result.returncode == 0:
            installed = True
            print("  [Setup] Dependencies installed from pyproject.toml")
        else:
            print(f"  [Setup] pip install -e . failed: {result.stderr[:200]}")

    # Try requirements files
    for req_file in sorted(ws.glob("requirements*.txt")):
        print(f"  [Setup] Installing from {req_file.name}...")
        result = subprocess.run(
            ["pip", "install", "-r", str(req_file), "--quiet"],
            capture_output=True, text=True, timeout=120, cwd=workspace,
        )
        if result.returncode == 0:
            installed = True
            print(f"  [Setup] Dependencies installed from {req_file.name}")
        else:
            print(f"  [Setup] pip install -r {req_file.name} failed: {result.stderr[:200]}")

    if not installed:
        print("  [Setup] No requirements files found, skipping dep install")


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
    target_dir = state.get("target_dir", "")

    print(f"[STAGE 4] Setting up workspace for run {run_id}...")

    # Resolve the effective directory: target_dir if set, else CWD
    if target_dir and Path(target_dir).is_dir():
        effective_dir = str(Path(target_dir).resolve())
        print(f"  Target dir: {effective_dir}")
    else:
        effective_dir = None  # signals "use CWD" to helpers
        if target_dir:
            print(f"  Target dir: {target_dir} (not found, falling back to CWD)")
        else:
            print("  Target dir: not set (using CWD)")

    # 1. Check available tools
    tools_available = _check_tools()
    print(f"  Tools: {', '.join(k for k, v in tools_available.items() if v)}")

    # 2. Create run directory with metadata (under target or CWD)
    run_dir = _create_run_dir(run_id, request, pipeline_path, base_dir=effective_dir)
    print(f"  Run dir: {run_dir}")

    # 3. Create git worktree (if in a git repo)
    worktree_path = None
    branch = None
    if tools_available.get("git"):
        worktree_path, branch = _setup_worktree(project_name, run_id, repo_dir=effective_dir)
        if worktree_path:
            print(f"  Worktree: {worktree_path}")
            print(f"  Branch: {branch}")
        else:
            # Worktree failed — fall back to target_dir (not CWD)
            if effective_dir and str(effective_dir) != str(Path.cwd()):
                worktree_path = str(effective_dir)
                print(f"  Worktree: using target dir directly (worktree creation failed)")
            else:
                print("  Worktree: skipped (not a git repo or creation failed)")
    else:
        print("  Worktree: skipped (git not available)")

    # 4. Scaffold stub files (only for greenfield — existing projects don't need stubs)
    scaffolded_files = []
    if task_plan and not target_dir:
        scaffolded_files = _scaffold_files(task_plan, worktree_path)
        if scaffolded_files:
            print(f"  Scaffolded {len(scaffolded_files)} files")
    else:
        print("  Scaffolding: skipped (no task plan)")

    # 5. Install dependencies in the workspace
    install_root = worktree_path or effective_dir
    if install_root and Path(install_root).is_dir():
        _install_deps(install_root)
    else:
        print("  [Setup] No workspace directory available, skipping dep install")

    # Build workspace_info (always propagate target_dir for downstream fallback)
    workspace_info = {
        "worktree_path": worktree_path,
        "branch": branch,
        "run_dir": run_dir,
        "tools_available": tools_available,
        "scaffolded_files": scaffolded_files,
        "target_dir": effective_dir or "",
    }

    print("[STAGE 4] Setup complete.")

    return {
        "current_stage": "setup",
        "workspace_info": workspace_info,
    }
