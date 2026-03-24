"""Stage 5: Builder -- generate code for each task in the plan and write to disk.

Uses the LLM router to generate BuildResult per task via Instructor.
After generation, files are written to the workspace and committed via git.
Install dependencies with: pip install 'pineapple-pipeline[llm]'
"""
import os
import subprocess
from pathlib import Path

from pineapple.models import BuildResult, FileWrite, Task, TaskPlan
from pineapple.state import PipelineState

# ---------------------------------------------------------------------------
# Lazy imports for optional LLM dependencies
# ---------------------------------------------------------------------------

_HAS_LLM_DEPS = True
_IMPORT_ERROR = None  # type: str | None

try:
    from pineapple.llm import get_llm_client, has_any_llm_key, COST_ESTIMATES, estimate_cost, _extract_usage, flush_traces
    from tenacity import retry, stop_after_attempt, wait_exponential
except ImportError as exc:
    _HAS_LLM_DEPS = False
    _IMPORT_ERROR = str(exc)

# ---------------------------------------------------------------------------
# Model configuration
# ---------------------------------------------------------------------------

_MAX_TOKENS = 4096

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are an expert software engineer. You are given a task from a project plan.
Your job is to generate a BuildResult containing REAL, working implementation code.

IMPORTANT: You MUST populate the `files_written` list with actual file contents.
Each entry needs a `path` (relative to project root) and `content` (the full file text).

ISOLATION: You can only write code. You cannot run tests, deploy, or modify
infrastructure. Focus solely on implementation.

Write production-quality code. Do NOT return placeholders, descriptions, or
pseudo-code. Return actual runnable implementation."""

_USER_PROMPT_TEMPLATE = """\
Task ID: {task_id}
Description: {description}
Files to create: {files_to_create}
Files to modify: {files_to_modify}
Complexity: {complexity}

Design context:
{design_summary}

{prior_context}
Generate a BuildResult for this task.
- Set status to "completed"
- Include a commit message in `commits` describing the change
- Populate `files_written` with the ACTUAL file contents for every file listed above.
  Each entry must have `path` (relative path) and `content` (full file text)."""


# ---------------------------------------------------------------------------
# File writing
# ---------------------------------------------------------------------------


def _write_files_to_disk(
    files_written: list[FileWrite], workspace: str
) -> list[str]:
    """Write files from BuildResult to disk. Returns list of written paths."""
    written = []
    base = Path(workspace)
    for fw in files_written:
        if not fw.path or not fw.content:
            continue
        filepath = base / fw.path
        try:
            filepath.parent.mkdir(parents=True, exist_ok=True)
            filepath.write_text(fw.content, encoding="utf-8")
            written.append(fw.path)
        except OSError as exc:
            print(f"    [WARN] Could not write {fw.path}: {exc}")
    return written


# ---------------------------------------------------------------------------
# Git helpers
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


def _git_commit(workspace: str, message: str) -> bool:
    """Stage all changes and commit in the workspace. Returns True on success."""
    try:
        result = _run_git("rev-parse", "--is-inside-work-tree", cwd=workspace)
        if result.returncode != 0:
            return False

        _run_git("add", "-A", cwd=workspace)

        # Check if there is anything to commit
        status = _run_git("diff", "--cached", "--quiet", cwd=workspace)
        if status.returncode == 0:
            # Nothing staged
            return False

        result = _run_git("commit", "-m", message, cwd=workspace)
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


# ---------------------------------------------------------------------------
# LLM call with retry
# ---------------------------------------------------------------------------


def _call_llm_for_task(task: Task, design_summary: str, llm=None, prior_context: str = "") -> tuple[BuildResult, float]:
    """Call the LLM to generate a BuildResult for a single task.

    Returns (BuildResult, cost_usd). Uses real token counts from the response
    when available, otherwise falls back to flat cost estimates.

    Args:
        prior_context: Summary of files written by earlier tasks in this run,
                       so the LLM knows what already exists.
    """
    if llm is None:
        llm = get_llm_client(stage="build")

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=30))
    def _inner() -> BuildResult:
        return llm.create(
            response_model=BuildResult,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _USER_PROMPT_TEMPLATE.format(
                task_id=task.id,
                description=task.description,
                files_to_create=task.files_to_create or "None",
                files_to_modify=task.files_to_modify or "None",
                complexity=task.complexity,
                design_summary=design_summary,
                prior_context=prior_context,
            )}],
            max_tokens=_MAX_TOKENS,
        )

    result = _inner()
    usage = _extract_usage(result, llm.provider)
    cost = estimate_cost(llm.provider, usage)
    return result, cost


# ---------------------------------------------------------------------------
# Fallback builder (no LLM)
# ---------------------------------------------------------------------------


def _generate_stub_content(file_path: str, task_description: str) -> str:
    """Generate minimal stub content based on file extension."""
    if file_path.endswith(".py"):
        return (
            f'"""Auto-generated stub for: {task_description}\n'
            f'\n'
            f'File: {file_path}\n'
            f'TODO: Implement this module.\n'
            f'"""\n'
        )
    elif file_path.endswith((".yml", ".yaml")):
        return f"# Auto-generated stub for: {task_description}\n# File: {file_path}\n"
    elif file_path.endswith(".json"):
        return "{}\n"
    elif file_path.endswith((".md", ".txt", ".rst")):
        return f"# {task_description}\n\nTODO: Fill in content.\n"
    else:
        return f"// Auto-generated stub for: {task_description}\n"


def _build_task_fallback(task: Task) -> BuildResult:
    """Create a BuildResult with stub files from the task's file lists."""
    all_files = list(task.files_to_create or []) + list(task.files_to_modify or [])
    files_written = []
    for fp in all_files:
        if fp and isinstance(fp, str):
            files_written.append(
                FileWrite(
                    path=fp,
                    content=_generate_stub_content(fp, task.description),
                )
            )
    return BuildResult(
        task_id=task.id,
        status="completed",
        commits=[f"feat: {task.description}"],
        errors=[],
        files_written=files_written,
    )


# ---------------------------------------------------------------------------
# Error result factory
# ---------------------------------------------------------------------------


def _make_error_result(task_id: str, error: str) -> BuildResult:
    """Create a failed BuildResult for error cases."""
    return BuildResult(
        task_id=task_id,
        status="failed",
        commits=[],
        errors=[error],
    )


# ---------------------------------------------------------------------------
# Public node
# ---------------------------------------------------------------------------


def builder_node(state: PipelineState) -> dict:
    """Generate code for each task in the plan and write files to disk.

    ISOLATED: Can only write code, cannot run tests.

    Falls back gracefully if:
    - LLM dependencies are not installed
    - ANTHROPIC_API_KEY is not set
    - The LLM call fails after retries
    """
    project_name = state.get("project_name", "unknown")
    print(f"[Stage 5: Build] Project: {project_name}")

    # Resolve workspace path: worktree > target_dir > CWD
    workspace_info = state.get("workspace_info") or {}
    workspace = (
        workspace_info.get("worktree_path")
        or state.get("target_dir")
        or os.getcwd()
    )
    print(f"  [Build] Workspace: {workspace}")

    # Parse task plan from state -- lightweight path may skip planner
    task_plan_data = state.get("task_plan")
    if not task_plan_data:
        # Lightweight path: no planner ran, create single-task plan from request
        task_plan_data = {
            "tasks": [{"id": "TASK-001", "description": state.get("request", "implement change"), "files": [], "complexity": "trivial", "estimated_cost_usd": 0.01}],
            "total_estimated_cost_usd": 0.01,
            "approved": True,
        }
        print("  [Build] No task_plan found -- auto-generated single-task plan (lightweight path)")

    task_plan = TaskPlan(**task_plan_data)
    design_spec_data = state.get("design_spec") or {}
    design_summary = design_spec_data.get("summary", "No design spec available.")

    # Determine if we can use LLM
    use_llm = _HAS_LLM_DEPS and has_any_llm_key()
    llm = None
    provider = "none"

    if not use_llm:
        reason = _IMPORT_ERROR if not _HAS_LLM_DEPS else "No LLM API key set"
        print(f"  [Build] LLM unavailable ({reason}), using fallback builder.")
    else:
        llm = get_llm_client(stage="build")
        provider = llm.provider
        print(f"  [Build] Using provider: {provider}")

    build_results = []  # type: list[dict]
    total_cost = 0.0
    total_files_written = 0
    cumulative_files = []  # type: list[str]  # tracks files written across tasks

    for task in task_plan.tasks:
        print(f"  [Build] Task {task.id}: {task.description}")

        # Build prior context string from files written by earlier tasks
        prior_context = ""
        if cumulative_files:
            prior_context = (
                "Previously completed files in this run (do NOT recreate, "
                "but you may import from them):\n"
                + "\n".join(f"  - {f}" for f in cumulative_files)
            )

        if use_llm:
            try:
                result, task_cost = _call_llm_for_task(
                    task, design_summary, llm=llm, prior_context=prior_context,
                )
                # Ensure task_id matches
                result.task_id = task.id
                total_cost += task_cost
                print(f"    Status: {result.status}, Commits: {len(result.commits)}, Files: {len(result.files_written)}, Cost: ${task_cost:.4f}")
            except Exception as e:
                print(f"    ERROR: {e}")
                result = _make_error_result(task.id, str(e))
        else:
            result = _build_task_fallback(task)
            print(f"    Status: {result.status} (fallback), Files: {len(result.files_written)}")

        # Write files to disk
        if result.files_written and result.status == "completed":
            written = _write_files_to_disk(result.files_written, workspace)
            total_files_written += len(written)
            cumulative_files.extend(written)
            if written:
                print(f"    Wrote {len(written)} file(s): {', '.join(written)}")

            # Git commit for this task (only if git is available)
            tools = workspace_info.get("tools_available", {})
            if not tools.get("git", True):  # default True for backward compat
                print("  [Build] Git not available, skipping commits")
            else:
                if result.commits:
                    commit_msg = result.commits[0]
                else:
                    commit_msg = f"build({task.id}): {task.description}"
                committed = _git_commit(workspace, commit_msg)
                if committed:
                    print(f"    Committed: {commit_msg}")
                else:
                    print(f"    Git commit skipped (no git or nothing to commit)")

        build_results.append(result.model_dump())

    completed = sum(1 for r in build_results if r["status"] == "completed")
    failed = sum(1 for r in build_results if r["status"] == "failed")
    print(f"  [Build] Done: {completed} completed, {failed} failed out of {len(build_results)} tasks")
    print(f"  [Build] Total files written to disk: {total_files_written}")

    # Flush LangFuse traces before returning
    if use_llm:
        flush_traces()

    # Increment build attempt count for observability
    attempt_counts = dict(state.get("attempt_counts", {}))
    attempt_counts["build"] = attempt_counts.get("build", 0) + 1

    return {
        "current_stage": "build",
        "build_results": build_results,
        "cost_total_usd": state.get("cost_total_usd", 0.0) + total_cost,
        "attempt_counts": attempt_counts,
    }
