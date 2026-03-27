"""Stage 5: Builder -- generate code for each task in the plan and write to disk.

Uses the LLM router to generate BuildResult per task via Instructor.
After generation, files are written to the workspace and committed via git.
Install dependencies with: pip install 'pineapple-pipeline[llm]'
"""
import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from pineapple.models import BuildResult, FileWrite, Task, TaskPlan
from pineapple.state import PipelineState

# ---------------------------------------------------------------------------
# Lazy imports for optional LLM dependencies
# ---------------------------------------------------------------------------

_HAS_LLM_DEPS = True
_IMPORT_ERROR = None  # type: str | None

try:
    from pineapple.llm import call_with_retry, get_llm_client, has_any_llm_key, flush_traces
except ImportError as exc:
    _HAS_LLM_DEPS = False
    _IMPORT_ERROR = str(exc)

# ---------------------------------------------------------------------------
# Model configuration
# ---------------------------------------------------------------------------

_MAX_TOKENS = 8192
_MAX_CONCURRENT = int(os.environ.get("PINEAPPLE_MAX_CONCURRENT", "4"))

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are an expert software engineer. You are given a task from a project plan.
Your job is to generate a BuildResult containing REAL, working implementation code.

CRITICAL DOMAIN CONTEXT: "KFS" stands for "Kinetic Forge Studio" -- a kinetic sculpture
design application. It is NOT Kubernetes. A ".kfs.yaml" manifest describes 3D geometry,
materials, motion parameters, and simulation settings for kinetic sculptures.
Do NOT generate Kubernetes-related code, containers, volumes, or workload definitions.

IMPORTANT: You MUST populate the `files_written` list with actual file contents.
Each entry needs a `path` (relative to project root) and `content` (the full file text).

ISOLATION: You can only write code. You cannot run tests, deploy, or modify
infrastructure. Focus solely on implementation.

PATH ENFORCEMENT: You MUST write files to the exact paths listed in files_to_create
and files_to_modify. Do NOT invent alternative paths, rename directories, or create
duplicate module trees. If a path is src/kfs_manifest/models.py, write EXACTLY there.

TEST REQUIREMENTS: When generating test files, tests MUST import classes and functions
from the actual source modules (e.g., `from src.kfs_manifest.models import KFSManifest`).
Do NOT define mock classes inline. Do NOT redefine the classes being tested.
Use pytest fixtures and unittest.mock only for external dependencies (APIs, databases).

Write production-quality code. Do NOT return placeholders, descriptions, or
pseudo-code. Return actual runnable implementation.

If an architecture document is provided, you MUST:
- Write files to the EXACT paths specified in the architecture
- Use the EXACT class/function names from the architecture interfaces
- Import from the EXACT modules specified in the architecture
- Do NOT create files at different paths or with different names"""

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
    files_written: list[FileWrite], workspace: str, own_files: set[str] | None = None,
) -> list[str]:
    """Write files from BuildResult to disk. Returns list of written paths.

    Args:
        files_written: Files to write from LLM output.
        workspace: Root directory to write into.
        own_files: Set of relative paths already written by THIS pipeline run.
                   Files in this set are always overwritten (they are our own
                   previous attempt from an earlier pass/retry).
    """
    written = []
    base = Path(workspace)
    if own_files is None:
        own_files = set()
    for fw in files_written:
        if not fw.path or not fw.content:
            continue
        filepath = base / fw.path
        # If this file was written by a previous pass in this same run,
        # always allow overwrite -- it's our own retry output.
        if filepath.exists() and fw.path in own_files:
            print(f"    [RE-OVERWRITE] {fw.path} (own file from earlier pass)")
        elif filepath.exists() and filepath.stat().st_size > 0:
            # Don't overwrite existing project files (e.g. pyproject.toml),
            # but DO overwrite stubs (Setup scaffolds or fallback stubs).
            try:
                existing = filepath.read_text(encoding="utf-8", errors="replace")
            except OSError:
                existing = ""
            is_stub = (
                filepath.stat().st_size < 200
                and ("TODO" in existing or "Auto-generated stub" in existing
                     or "generated by Pineapple Setup" in existing
                     or existing.strip() == "pass")
            )
            if not is_stub:
                print(f"    [SKIP] {fw.path} already exists ({filepath.stat().st_size} bytes)")
                continue
            print(f"    [OVERWRITE STUB] {fw.path} ({filepath.stat().st_size} bytes)")
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
        llm: Optional pre-created LLMClient to reuse across tasks. If None,
             a new client is created for this call.
        prior_context: Summary of files written by earlier tasks in this run,
                       so the LLM knows what already exists.
    """
    result, _provider, cost = call_with_retry(
        stage="build",
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
        client=llm,
    )
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
# Parallel task grouping
# ---------------------------------------------------------------------------


def _group_parallel_tasks(tasks: list[Task]) -> list[list[Task]]:
    """Group tasks into batches that can run in parallel.

    Tasks with no file overlap can run simultaneously.
    Tasks are grouped greedily: each batch contains tasks whose
    files_to_create and files_to_modify don't overlap.
    """
    batches: list[list[Task]] = []

    for task in tasks:
        task_files = set(task.files_to_create or []) | set(task.files_to_modify or [])

        # Try to add to the last batch
        placed = False
        if batches:
            batch = batches[-1]
            batch_files: set[str] = set()
            for t in batch:
                batch_files |= set(t.files_to_create or []) | set(t.files_to_modify or [])

            if not task_files & batch_files:  # No overlap
                batch.append(task)
                placed = True

        if not placed:
            batches.append([task])

    return batches


# ---------------------------------------------------------------------------
# Per-task execution helpers
# ---------------------------------------------------------------------------


def _extract_architecture_context(task: Task, design_spec: dict) -> str:
    """Extract the architecture section relevant to a task from the design spec.

    Looks for the raw architecture document and finds the section(s) that mention
    the task's files or description keywords.

    Returns an empty string when no architecture context is available.
    """
    raw_architecture = design_spec.get("_raw_document", "")
    if not raw_architecture:
        return ""

    task_files = set(task.files_to_create or []) | set(task.files_to_modify or [])
    if not task_files and not task.description:
        return raw_architecture  # No filter criteria — return everything

    # Collect lines from the document that are near mentions of the task's files
    # or description keywords.  We use a simple sliding-window approach: include
    # any paragraph/section that contains at least one matching token.
    description_keywords = set(
        w.lower() for w in task.description.split() if len(w) > 4
    )
    file_basenames = {Path(f).name for f in task_files} | {Path(f).stem for f in task_files}
    search_tokens = {f.lower() for f in file_basenames} | description_keywords

    paragraphs = raw_architecture.split("\n\n")
    relevant = []
    for para in paragraphs:
        para_lower = para.lower()
        if any(tok in para_lower for tok in search_tokens):
            relevant.append(para)

    if not relevant:
        # Nothing matched — return first 3000 chars as generic context
        return raw_architecture[:3000]

    excerpt = "\n\n".join(relevant)
    if len(excerpt) > 4000:
        excerpt = excerpt[:4000] + "\n... (truncated)"
    return excerpt


def _build_one_task(
    task: Task, workspace: str, design_summary: str,
    cumulative_files: list[FileWrite], review_result: dict,
    verify_record: dict, run_files: set[str], workspace_info: dict,
    use_llm: bool, llm, builder_mode: str,
    design_spec: dict | None = None,
    skip_tests: bool = False,
) -> tuple[BuildResult, float]:
    """Build a single task using either single-shot or agent mode."""
    print(f"  [Build] Task {task.id}: {task.description}")

    if design_spec is None:
        design_spec = {}

    # Build prior context
    prior_context = ""
    if cumulative_files:
        prior_context = "Previously completed files in this run (do NOT recreate, but you may import from them):\n"
        for fw in cumulative_files:
            content_preview = fw.content[:4000] if fw.content else "(empty)"
            prior_context += f"\n--- {fw.path} ---\n{content_preview}\n"
    if review_result or verify_record:
        task_feedback = _get_task_feedback(task, review_result, verify_record)
        if task_feedback:
            prior_context = task_feedback + prior_context

    if not use_llm:
        result = _build_task_fallback(task)
        print(f"    Status: {result.status} (fallback), Files: {len(result.files_written)}")
        return result, 0.0

    if builder_mode == "agent":
        architecture_context = _extract_architecture_context(task, design_spec)
        return _build_task_agent(task, workspace, design_summary, prior_context, architecture_context=architecture_context, skip_tests=skip_tests)
    else:
        return _build_task_single_shot(task, design_summary, llm, prior_context)


def _build_task_single_shot(
    task: Task, design_summary: str, llm, prior_context: str,
) -> tuple[BuildResult, float]:
    """Original single-shot LLM call."""
    try:
        result, task_cost = _call_llm_for_task(task, design_summary, llm=llm, prior_context=prior_context)
        result.task_id = task.id
        print(f"    Status: {result.status}, Commits: {len(result.commits)}, Files: {len(result.files_written)}, Cost: ${task_cost:.4f}")
        return result, task_cost
    except Exception as e:
        print(f"    ERROR: {e}")
        return _make_error_result(task.id, str(e)), 0.0


def _build_task_agent(
    task: Task, workspace: str, design_summary: str, prior_context: str,
    architecture_context: str = "",
    skip_tests: bool = False,
) -> tuple[BuildResult, float]:
    """Multi-turn agent with tools."""
    try:
        from pineapple.agents.agent_builder import run_agent_task
    except ImportError as exc:
        print(f"    Agent builder not available: {exc}, falling back to single-shot")
        return _build_task_single_shot(task, design_summary, None, prior_context)

    try:
        # Scale max_turns by task complexity
        _TURNS_BY_COMPLEXITY = {"trivial": 8, "standard": 15, "complex": 25}
        effective_max_turns = _TURNS_BY_COMPLEXITY.get(task.complexity, 15)

        files_written, cost, summary = run_agent_task(
            task_description=task.description,
            workspace=workspace,
            design_summary=design_summary,
            prior_context=prior_context,
            files_to_create=task.files_to_create,
            files_to_modify=task.files_to_modify,
            architecture_context=architecture_context,
            workspace_manifest=architecture_context,  # orchestrator context doubles as workspace manifest
            allowed_paths=list(set((task.files_to_create or []) + (task.files_to_modify or []))),
            max_turns=effective_max_turns,
            skip_tests=skip_tests,
        )

        # Detect incomplete tasks: agent hit max turns without calling task_complete
        task_incomplete = "max turns reached" in summary.lower() or summary.startswith("Task incomplete")
        if task_incomplete:
            # Save partial work if the agent wrote real files
            has_real_files = any(
                f.get("content", "") and len(f.get("content", "")) > 50
                for f in files_written
            )
            if has_real_files:
                print(f"    [Build] Task {task.id} INCOMPLETE but has {len(files_written)} files — saving partial work")
                result = BuildResult(
                    task_id=task.id,
                    status="completed",
                    commits=[f"feat(partial): {task.description[:50]}"],
                    errors=["Task incomplete — max turns reached, partial work saved"],
                    files_written=[FileWrite(path=f["path"], content=f["content"]) for f in files_written],
                )
                print(f"    Status: completed (partial), Files: {len(files_written)}, Cost: ${cost:.4f}")
            else:
                print(f"    [Build] Task {task.id} INCOMPLETE — no real files written")
                result = BuildResult(
                    task_id=task.id,
                    status="failed",
                    commits=[],
                    errors=["Task incomplete — max turns reached without producing files"],
                    files_written=[FileWrite(path=f["path"], content=f["content"]) for f in files_written],
                )
                print(f"    Status: failed (agent incomplete), Files: {len(files_written)}, Cost: ${cost:.4f}")
        else:
            # Convert to BuildResult
            result = BuildResult(
                task_id=task.id,
                status="completed",
                commits=[f"feat: {summary[:100]}"],
                errors=[],
                files_written=[FileWrite(path=f["path"], content=f["content"]) for f in files_written],
            )
            print(f"    Status: completed (agent), Files: {len(files_written)}, Cost: ${cost:.4f}")
        return result, cost
    except Exception as e:
        print(f"    ERROR (agent): {e}")
        return _make_error_result(task.id, str(e)), 0.0


def _process_build_result(
    result: BuildResult, workspace: str, run_files: set[str],
    cumulative_files: list[FileWrite], workspace_info: dict,
) -> int:
    """Write files to disk and git commit. Returns count of files written."""
    files_written_count = 0
    if result.status == "failed":
        print(f"    [Build] Task {result.task_id} INCOMPLETE — not committing partial work")
        return 0
    if result.files_written and result.status == "completed":
        written = _write_files_to_disk(result.files_written, workspace, own_files=run_files)
        files_written_count = len(written)
        run_files.update(written)
        cumulative_files.extend(fw for fw in result.files_written if fw.path in written)
        if written:
            print(f"    Wrote {len(written)} file(s): {', '.join(written)}")

        tools = workspace_info.get("tools_available", {})
        if not tools.get("git", True):
            pass
        else:
            if result.commits:
                commit_msg = result.commits[0]
            else:
                commit_msg = f"build({result.task_id}): task completed"
            committed = _git_commit(workspace, commit_msg)
            if committed:
                print(f"    Committed: {commit_msg}")
            else:
                print(f"    Git commit skipped (no git or nothing to commit)")
    return files_written_count


# ---------------------------------------------------------------------------
# Keyword extraction helper for reviewer-task matching
# ---------------------------------------------------------------------------


def _extract_keywords(text: str) -> list[str]:
    """Extract significant keywords from task description for matching."""
    stop_words = {
        "the", "a", "an", "and", "or", "for", "to", "of", "in", "is", "it",
        "its", "this", "that", "with", "from", "by", "as", "on", "at", "be",
        "will", "can", "has", "have", "was", "are", "not", "but", "all",
        "their", "each", "which", "do", "how", "if", "up", "out", "no", "so",
        "what", "when", "who", "get", "set", "should", "would", "could",
        "also", "into", "than", "then", "them", "these", "those", "some",
        "any", "such", "only", "other", "new", "one", "our", "may", "like",
    }
    words = text.split()
    return [w for w in words if len(w) > 3 and w not in stop_words]


# ---------------------------------------------------------------------------
# Per-task retry feedback helpers
# ---------------------------------------------------------------------------


def _get_task_feedback(task: Task, review_result: dict, verify_record: dict) -> str:
    """Build retry feedback targeted to a specific task's files."""
    task_files = set(task.files_to_create or []) | set(task.files_to_modify or [])
    if not task_files:
        # No file info — give all feedback
        return _build_general_feedback(review_result, verify_record)

    feedback_parts = []

    # Filter reviewer issues to those mentioning this task's files
    for level, key in [("CRITICAL", "critical_issues"), ("IMPORTANT", "important_issues")]:
        issues = review_result.get(key, [])
        relevant = [i for i in issues if any(f in i for f in task_files)]
        # Also include issues that don't mention any specific file (general issues)
        general = [i for i in issues if not any("." in word and "/" in word for word in i.split())]
        combined = list(dict.fromkeys(relevant + general))  # dedupe, preserve order
        if combined:
            feedback_parts.append(f"{level} ISSUES:\n" + "\n".join(f"  - {i}" for i in combined))

    # Filter verifier failures
    layers = verify_record.get("layers", [])
    failed_layers = [l for l in layers if l.get("status") == "fail"]
    if failed_layers:
        layer_details = [f"  - {l.get('name', '?')}: {l.get('details', '')[:200]}" for l in failed_layers]
        feedback_parts.append("VERIFICATION FAILURES:\n" + "\n".join(layer_details))

    if not feedback_parts:
        return ""

    return (
        "\n\n=== RETRY FEEDBACK (targeted to this task) ===\n"
        "The previous build attempt had these issues. You MUST fix them in this attempt:\n\n"
        + "\n\n".join(feedback_parts)
        + "\n=== END RETRY FEEDBACK ===\n\n"
    )


def _build_general_feedback(review_result: dict, verify_record: dict) -> str:
    """Build general feedback when task has no file info."""
    feedback_parts = []
    for level, key in [("CRITICAL", "critical_issues"), ("IMPORTANT", "important_issues")]:
        issues = review_result.get(key, [])
        if issues:
            feedback_parts.append(f"{level} ISSUES:\n" + "\n".join(f"  - {i}" for i in issues))
    layers = verify_record.get("layers", [])
    failed = [l for l in layers if l.get("status") == "fail"]
    if failed:
        feedback_parts.append("VERIFICATION FAILURES:\n" + "\n".join(
            f"  - {l.get('name', '?')}: {l.get('details', '')[:200]}" for l in failed
        ))
    if not feedback_parts:
        return ""
    return (
        "\n\n=== RETRY FEEDBACK ===\n"
        "The previous build attempt had these issues. You MUST fix them in this attempt:\n\n"
        + "\n\n".join(feedback_parts)
        + "\n=== END RETRY FEEDBACK ===\n\n"
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

    # Resolve workspace path: worktree > target_dir > ERROR (never CWD)
    workspace_info = state.get("workspace_info") or {}
    workspace = (
        workspace_info.get("worktree_path")
        or state.get("target_dir")
        or None
    )

    if not workspace:
        raise RuntimeError(
            "Builder has no workspace: both worktree_path and target_dir are empty. "
            "Cannot fall back to CWD -- that would write to the pipeline repo."
        )

    # Guard: never write into the pipeline's own repo
    _pipeline_repo = os.path.normcase(str(Path(__file__).resolve().parents[3]))  # agents/ -> pineapple/ -> src/ -> repo root
    _resolved_workspace = os.path.normcase(str(Path(workspace).resolve()))
    if _resolved_workspace == _pipeline_repo or _resolved_workspace.startswith(_pipeline_repo + os.sep):
        raise RuntimeError(
            f"Builder would write to the pipeline repo ({_pipeline_repo}), not the target project. "
            f"Resolved workspace: {_resolved_workspace}. "
            "Check that target_dir or worktree_path points to the correct project."
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

    # Check builder mode: single_shot (default) or agent (multi-turn with tools)
    builder_mode = os.environ.get("PINEAPPLE_BUILDER", "single_shot")
    print(f"  [Build] Mode: {builder_mode}")

    build_results = []  # type: list[dict]
    total_cost = 0.0

    # Check if architecture-aware orchestration is available
    raw_architecture = design_spec_data.get("_raw_document", "")
    _MAX_CONCURRENT = int(os.environ.get("PINEAPPLE_MAX_CONCURRENT", "4"))
    if raw_architecture:
        print(f"  [Build] Architecture document found ({len(raw_architecture)} chars) — phased orchestration active")
        try:
            from pineapple.orchestrator import run_phased_build
            print("  [Build] Delegating to phased orchestrator")
            phased_results, phased_cost = run_phased_build(
                tasks=[t for t in task_plan.tasks],  # Task objects
                workspace=workspace,
                design_spec=design_spec_data,
                state=state,
                build_fn=_build_one_task,
                process_fn=_process_build_result,
                max_concurrent=_MAX_CONCURRENT,
            )
            build_results.extend([
                r.model_dump() if hasattr(r, "model_dump") else r
                for r in phased_results
            ])
            total_cost += phased_cost

            # Flush LangFuse traces before returning
            if _HAS_LLM_DEPS:
                try:
                    flush_traces()
                except Exception:
                    pass

            # Increment build attempt count for observability
            attempt_counts = dict(state.get("attempt_counts", {}))
            attempt_counts["build"] = attempt_counts.get("build", 0) + 1

            completed = sum(1 for r in build_results if r.get("status") == "completed")
            failed = sum(1 for r in build_results if r.get("status") == "failed")
            print(f"  [Build] Done (phased): {completed} completed, {failed} failed out of {len(build_results)} tasks")

            return {
                "current_stage": "build",
                "build_results": build_results,
                "cost_total_usd": state.get("cost_total_usd", 0.0) + total_cost,
                "attempt_counts": attempt_counts,
            }
        except ImportError:
            print("  [Build] Orchestrator not available, falling back to flat execution")

    if not raw_architecture:
        print("  [Build] No architecture document in design_spec — using flat execution")

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

    total_cost = 0.0
    total_files_written = 0
    cumulative_files = []  # type: list[FileWrite]  # tracks files written across tasks
    run_files: set[str] = set()  # paths written by THIS pipeline run (allow re-overwrite on retry)

    # On retry: seed run_files from previous build results so we can overwrite
    # files that were written in earlier passes (fixes the stuck retry loop).
    attempt_counts = state.get("attempt_counts", {})
    previous_results = state.get("build_results", [])

    attempt_count = attempt_counts.get("build", 0)
    if attempt_count > 0 and previous_results:
        # Check if reviewer flagged specific tasks via critical_issues.
        # Re-mark those completed tasks as "failed" so they get re-run.
        review_result_for_retry = state.get("review_result") or {}
        critical_issues = review_result_for_retry.get("critical_issues", [])

        if critical_issues:
            tasks_to_rerun: set[str] = set()
            for issue in critical_issues:
                issue_lower = issue.lower()
                for task in task_plan.tasks:
                    task_desc_lower = task.description.lower()
                    keywords = _extract_keywords(task_desc_lower)
                    if any(keyword in issue_lower for keyword in keywords):
                        tasks_to_rerun.add(task.id)

            if tasks_to_rerun:
                for r in previous_results:
                    if r["task_id"] in tasks_to_rerun and r.get("status") == "completed":
                        r["status"] = "failed"
                print(f"  [Build] Reviewer flagged {len(tasks_to_rerun)} tasks for re-run: {tasks_to_rerun}")

            # On attempt >= 3 with the same tasks still failing, inject extra fix context
            if attempt_count >= 3 and tasks_to_rerun:
                # Store the set so _build_one_task can inject it via review_result
                if "reviewer_rerun_tasks" not in review_result_for_retry:
                    review_result_for_retry = dict(review_result_for_retry)
                    review_result_for_retry["reviewer_rerun_tasks"] = list(tasks_to_rerun)
                    review_result_for_retry["reviewer_rerun_attempt"] = attempt_count
                    print(f"  [Build] Attempt {attempt_count + 1}: injecting escalated fix context for {tasks_to_rerun}")

        completed_ids = {r["task_id"] for r in previous_results if r.get("status") == "completed"}
        original_count = len(task_plan.tasks)
        task_plan.tasks = [t for t in task_plan.tasks if t.id not in completed_ids]
        # Carry forward completed results
        build_results = [r for r in previous_results if r.get("status") == "completed"]
        print(f"  [Build] Retry: {len(completed_ids)} tasks already completed, re-running {len(task_plan.tasks)} of {original_count}")
        for prev in previous_results:
            for fw in prev.get("files_written", []):
                path = fw.get("path", "") if isinstance(fw, dict) else getattr(fw, "path", "")
                if path:
                    run_files.add(path)
        if run_files:
            print(f"  [Build] Retry attempt {attempt_count + 1}: {len(run_files)} files from previous pass marked for overwrite")
    else:
        build_results = []  # type: list[dict]

    # On retry: extract reviewer/verifier results for per-task feedback injection
    review_result: dict = {}
    verify_record: dict = {}
    if attempt_counts.get("build", 0) > 0:
        review_result = state.get("review_result") or {}
        verify_record = state.get("verify_record") or {}
        critical = review_result.get("critical_issues", [])
        important = review_result.get("important_issues", [])
        if critical or important or verify_record.get("layers"):
            print(f"  [Build] Retry attempt: per-task feedback enabled ({len(critical)} critical, {len(important)} important issues)")

    # Group tasks for parallel execution
    task_batches = _group_parallel_tasks(task_plan.tasks)
    parallel_tasks = sum(len(b) for b in task_batches if len(b) > 1)
    if parallel_tasks > 0:
        print(f"  [Build] Task batches: {len(task_batches)} ({parallel_tasks} tasks can run in parallel)")

    for batch_idx, batch in enumerate(task_batches):
        if len(batch) == 1:
            # Single task -- run directly
            task = batch[0]
            result, task_cost = _build_one_task(
                task, workspace, design_summary, cumulative_files,
                review_result, verify_record, run_files, workspace_info,
                use_llm, llm, builder_mode,
                design_spec=design_spec_data,
            )
            total_cost += task_cost
            build_results.append(result.model_dump())
            total_files_written += _process_build_result(
                result, workspace, run_files, cumulative_files, workspace_info,
            )
        else:
            # Parallel batch
            print(f"  [Build] Running batch {batch_idx+1} in parallel: {', '.join(t.id for t in batch)}")
            max_workers = min(len(batch), 3)  # Cap at 3 concurrent
            futures = {}

            _semaphore = threading.Semaphore(_MAX_CONCURRENT)

            def _rate_limited_build(task, *args, **kwargs):
                with _semaphore:
                    return _build_one_task(task, *args, **kwargs)

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                for task in batch:
                    future = executor.submit(
                        _rate_limited_build,
                        task, workspace, design_summary, cumulative_files,
                        review_result, verify_record, run_files, workspace_info,
                        use_llm, llm, builder_mode,
                        design_spec_data,
                    )
                    futures[future] = task

                for future in as_completed(futures):
                    task = futures[future]
                    try:
                        result, task_cost = future.result()
                        total_cost += task_cost
                        build_results.append(result.model_dump())
                        total_files_written += _process_build_result(
                            result, workspace, run_files, cumulative_files, workspace_info,
                        )
                    except Exception as e:
                        print(f"    ERROR: Task {task.id}: {e}")
                        build_results.append(_make_error_result(task.id, str(e)).model_dump())

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
