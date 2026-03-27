"""Orchestrator -- phase-based build execution with architecture awareness.

Sits between plan_node and builder_node. Groups tasks into dependency phases
from the architecture design, runs phases sequentially, and feeds output
from completed phases into subsequent phases as context.

The architecture defines build phases (e.g. Foundation -> Execution ->
Intelligence -> Interface -> Verification). The orchestrator:

1. Parses phase definitions from the architecture's _raw_document or components
2. Maps each task to a phase via component ID / name matching
3. Executes phases sequentially -- Phase 1 completes before Phase 2 starts
4. After each phase, collects written code and injects it as context for the next
5. Validates that output files match the architecture contract
6. Updates MANIFEST.yaml with stage completion status
"""
from __future__ import annotations

import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Phase extraction
# ---------------------------------------------------------------------------

# Regex for "Phase N (Label): SC-XX Component, SC-YY Component"
_PHASE_RE = re.compile(
    r"Phase\s+(\d+)\s*(?:\([^)]*\))?\s*:\s*(.+)",
    re.IGNORECASE,
)

# Regex to pull SC-XX identifiers from text
_COMPONENT_ID_RE = re.compile(r"SC-\d{2,3}", re.IGNORECASE)


def extract_phases_from_architecture(design_spec: dict) -> list[list[str]]:
    """Parse the architecture's dependency graph into ordered phase groups.

    Reads ``_raw_document`` for explicit ``Phase N`` markers first.  Falls back
    to inferring phases from the ``components`` list when the raw doc is not
    available or contains no phase markers.

    Returns
    -------
    list[list[str]]
        Ordered list of phases, each phase being a list of component IDs
        (e.g. ``["SC-01", "SC-05"]``).
    """
    raw = design_spec.get("_raw_document", "")
    if raw:
        phases = _phases_from_raw_document(raw)
        if phases:
            return phases

    # Fallback: treat every component as its own phase (sequential)
    components = design_spec.get("components", [])
    if components:
        return _phases_from_components(components)

    return []


def _phases_from_raw_document(raw: str) -> list[list[str]]:
    """Extract phases from explicit Phase markers in the raw architecture doc.

    Handles multi-line phase blocks where SC-XX IDs appear on separate lines
    below the Phase header.  Scans all lines between consecutive Phase headers
    (or until a blank line / section header) to capture every component.
    """
    # Find all Phase header positions
    phase_headers: list[tuple[int, int, int]] = []  # (phase_num, start_pos, end_of_header_line)
    for match in re.finditer(
        r"^[ \t]*Phase\s+(\d+)\s*(?:\([^)]*\))?\s*:", raw, re.IGNORECASE | re.MULTILINE,
    ):
        phase_num = int(match.group(1))
        phase_headers.append((phase_num, match.start(), match.end()))

    if not phase_headers:
        return []

    phase_map: dict[int, list[str]] = {}

    for i, (phase_num, _start, header_end) in enumerate(phase_headers):
        # Block extends to the next Phase header or a section boundary (## or ```)
        if i + 1 < len(phase_headers):
            block_end = phase_headers[i + 1][1]
        else:
            # Look for the next blank-line-followed-by-non-indented-text or ```
            rest = raw[header_end:]
            end_match = re.search(r"\n(?:```|##|\S)", rest)
            block_end = header_end + end_match.start() if end_match else len(raw)

        block = raw[header_end:block_end]
        component_ids = _COMPONENT_ID_RE.findall(block)
        # Only keep IDs from the component lines, not from "depends on" annotations
        # Strategy: first pass gets SC-XX at line start (the primary components)
        primary_ids: list[str] = []
        for line in block.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            line_ids = _COMPONENT_ID_RE.findall(stripped)
            if line_ids:
                # First SC-XX on the line is the primary component for this phase;
                # subsequent ones are dependency references
                primary_ids.append(line_ids[0].upper())

        if primary_ids:
            phase_map.setdefault(phase_num, []).extend(primary_ids)

    if not phase_map:
        return []

    # Return phases in numeric order, deduplicating IDs within each phase
    ordered = []
    for key in sorted(phase_map.keys()):
        seen: set[str] = set()
        deduped: list[str] = []
        for cid in phase_map[key]:
            if cid not in seen:
                seen.add(cid)
                deduped.append(cid)
        ordered.append(deduped)
    return ordered


def _phases_from_components(components: list[dict]) -> list[list[str]]:
    """Infer phases from the components list when no raw doc is available.

    Groups components that share the same first digit in their SC-XX ID into
    the same phase.  If no SC-XX IDs are found, each component becomes its
    own phase.
    """
    phase_groups: dict[int, list[str]] = {}
    ungrouped: list[str] = []

    for comp in components:
        name = comp.get("name", "")
        ids = _COMPONENT_ID_RE.findall(name)
        if not ids:
            # Try description
            ids = _COMPONENT_ID_RE.findall(comp.get("description", ""))
        if ids:
            cid = ids[0].upper()
            # Use component number as key for deterministic ordering
            num = int(re.search(r"\d+", cid).group())
            phase_groups[num] = [cid]
        else:
            ungrouped.append(name)

    if phase_groups:
        result = [phase_groups[k] for k in sorted(phase_groups.keys())]
        if ungrouped:
            result.append(ungrouped)
        return result

    # No IDs at all -- one component per phase
    return [[comp.get("name", f"component-{i}")] for i, comp in enumerate(components)]


# ---------------------------------------------------------------------------
# Task-to-phase mapping
# ---------------------------------------------------------------------------

# Common component name patterns for fuzzy matching
_COMPONENT_NAMES: dict[str, list[str]] = {
    "SC-01": ["module manager", "modulemanager", "mod_manager"],
    "SC-02": ["module executor", "moduleexecutor", "mod_executor"],
    "SC-03": ["vlad runner", "vladrunner", "vlad", "validator"],
    "SC-04": ["three.js", "threejs", "3d viewer", "viewport"],
    "SC-05": ["context persistence", "context_persistence", "state persistence"],
    "SC-06": ["durga", "intelligence", "ai engine"],
    "SC-07": ["mcp tools", "mcp_tools", "mcp server"],
    "SC-08": ["manifest gen", "manifest_gen", "manifest generator"],
    "SC-09": ["contract tests", "contract_tests", "integration tests"],
    "SC-10": ["observability", "metrics", "telemetry", "logging"],
}


def map_tasks_to_phases(
    tasks: list[dict],
    phases: list[list[str]],
) -> list[list[dict]]:
    """Assign each task to its phase based on component ID matching.

    Matching strategy (in priority order):
    1. Exact SC-XX ID match in task description, files_to_create, or files_to_modify
    2. Fuzzy match on component name keywords
    3. Unmatched tasks go to the last phase

    Parameters
    ----------
    tasks:
        List of task dicts (from TaskPlan.tasks serialized).
    phases:
        Ordered phase groups from :func:`extract_phases_from_architecture`.

    Returns
    -------
    list[list[dict]]
        Tasks grouped by phase, same ordering as *phases*.
        Empty phases are preserved (returned as empty lists).
    """
    if not phases:
        # No phase info -- return all tasks in a single phase
        return [tasks] if tasks else []

    # Build reverse map: component_id -> phase_index
    id_to_phase: dict[str, int] = {}
    for phase_idx, component_ids in enumerate(phases):
        for cid in component_ids:
            id_to_phase[cid.upper()] = phase_idx

    result: list[list[dict]] = [[] for _ in phases]
    unmatched: list[dict] = []

    for task in tasks:
        phase_idx = _match_task_to_phase(task, id_to_phase, phases)
        if phase_idx is not None:
            result[phase_idx].append(task)
        else:
            unmatched.append(task)

    # Unmatched tasks go to the last phase
    if unmatched:
        result[-1].extend(unmatched)

    return result


def _match_task_to_phase(
    task: dict,
    id_to_phase: dict[str, int],
    phases: list[list[str]],
) -> int | None:
    """Try to match a single task to a phase index."""
    # Build a searchable text blob from the task
    desc = task.get("description", "")
    files_create = " ".join(task.get("files_to_create", []) or [])
    files_modify = " ".join(task.get("files_to_modify", []) or [])
    task_id = task.get("id", "")
    blob = f"{task_id} {desc} {files_create} {files_modify}"
    blob_upper = blob.upper()

    # Strategy 1: exact SC-XX ID match
    found_ids = _COMPONENT_ID_RE.findall(blob_upper)
    for cid in found_ids:
        cid_norm = cid.upper()
        if cid_norm in id_to_phase:
            return id_to_phase[cid_norm]

    # Strategy 2: fuzzy name match
    blob_lower = blob.lower()
    for cid, names in _COMPONENT_NAMES.items():
        cid_norm = cid.upper()
        if cid_norm not in id_to_phase:
            continue
        for name in names:
            if name in blob_lower:
                return id_to_phase[cid_norm]

    return None


# ---------------------------------------------------------------------------
# Per-task context building
# ---------------------------------------------------------------------------


def build_task_context(
    task: dict,
    design_spec: dict,
    completed_code: dict[str, str],
    existing_files: dict[str, str],
) -> str:
    """Build rich context string for a single task.

    Includes:
    - The component spec from architecture (files to create, interfaces)
    - Code from prior phases that this task depends on
    - Existing code from the target repo that this task extends
    - Exact file paths from architecture (not guessed)

    Parameters
    ----------
    task:
        The task dict being built.
    design_spec:
        Full architecture design spec dict.
    completed_code:
        ``{filepath: content}`` from prior phases.
    existing_files:
        ``{filepath: content}`` from the target repo on disk.

    Returns
    -------
    str
        Context block to prepend to the builder's prompt.
    """
    parts: list[str] = []

    # 1. Component spec from architecture
    component_spec = _find_component_spec(task, design_spec)
    if component_spec:
        parts.append("=== ARCHITECTURE COMPONENT SPEC ===")
        parts.append(f"Component: {component_spec.get('name', 'unknown')}")
        parts.append(f"Description: {component_spec.get('description', '')}")
        comp_files = component_spec.get("files", [])
        if comp_files:
            parts.append(f"Files defined in architecture: {', '.join(comp_files)}")
        comp_libs = component_spec.get("libraries", [])
        if comp_libs:
            parts.append(f"Libraries: {', '.join(comp_libs)}")
        parts.append("")

    # 2. Code from prior phases (dependencies)
    task_files = set(task.get("files_to_create", []) or []) | set(task.get("files_to_modify", []) or [])
    relevant_prior = _find_relevant_prior_code(task, completed_code, task_files)
    if relevant_prior:
        parts.append("=== CODE FROM PRIOR PHASES (available for import) ===")
        for fpath, content in relevant_prior.items():
            # Truncate very large files
            preview = content[:4000] if len(content) > 4000 else content
            parts.append(f"--- {fpath} ---")
            parts.append(preview)
            if len(content) > 4000:
                parts.append(f"... ({len(content)} bytes total, truncated)")
            parts.append("")

    # 3. Existing code from target repo that this task modifies
    for fpath in task.get("files_to_modify", []) or []:
        if fpath in existing_files:
            content = existing_files[fpath]
            preview = content[:4000] if len(content) > 4000 else content
            if not parts or parts[-1] != "=== EXISTING CODE TO EXTEND ===":
                parts.append("=== EXISTING CODE TO EXTEND ===")
            parts.append(f"--- {fpath} ---")
            parts.append(preview)
            if len(content) > 4000:
                parts.append(f"... ({len(content)} bytes total, truncated)")
            parts.append("")

    if not parts:
        return ""

    return "\n".join(parts)


def _find_component_spec(task: dict, design_spec: dict) -> dict | None:
    """Find the architecture component spec that matches this task."""
    components = design_spec.get("components", [])
    if not components:
        return None

    desc = task.get("description", "")
    task_id = task.get("id", "")
    blob = f"{task_id} {desc}".upper()

    # Try SC-XX ID match first
    found_ids = _COMPONENT_ID_RE.findall(blob)
    for comp in components:
        comp_name = comp.get("name", "").upper()
        comp_desc = comp.get("description", "").upper()
        comp_blob = f"{comp_name} {comp_desc}"
        for cid in found_ids:
            if cid.upper() in comp_blob:
                return comp

    # Fuzzy name match
    blob_lower = blob.lower()
    for comp in components:
        comp_name_lower = comp.get("name", "").lower()
        # Check if significant words from component name appear in task
        words = [w for w in comp_name_lower.split() if len(w) > 3]
        if words and sum(1 for w in words if w in blob_lower) >= len(words) * 0.5:
            return comp

    return None


def _find_relevant_prior_code(
    task: dict,
    completed_code: dict[str, str],
    task_files: set[str],
) -> dict[str, str]:
    """Select code from prior phases that this task likely depends on.

    Heuristics:
    - Files in the same package/directory as the task's files
    - __init__.py files from parent packages
    - Files explicitly imported (checked via task description keywords)
    """
    if not completed_code:
        return {}

    relevant: dict[str, str] = {}

    # Compute directories this task touches
    task_dirs: set[str] = set()
    for fpath in task_files:
        parent = str(Path(fpath).parent)
        task_dirs.add(parent)
        # Also add grandparent for cross-module awareness
        grandparent = str(Path(parent).parent)
        if grandparent != ".":
            task_dirs.add(grandparent)

    desc_lower = task.get("description", "").lower()

    for fpath, content in completed_code.items():
        fpath_obj = Path(fpath)
        fpath_parent = str(fpath_obj.parent)

        # Include if in same directory tree
        if fpath_parent in task_dirs:
            relevant[fpath] = content
            continue

        # Include __init__.py from related packages
        if fpath_obj.name == "__init__.py" and any(
            fpath_parent in td or td.startswith(fpath_parent)
            for td in task_dirs
        ):
            relevant[fpath] = content
            continue

        # Include if task description mentions the module name
        stem = fpath_obj.stem
        if len(stem) > 3 and stem in desc_lower:
            relevant[fpath] = content

    return relevant


# ---------------------------------------------------------------------------
# Phase output collection
# ---------------------------------------------------------------------------


def collect_phase_output(workspace: str, phase_tasks: list[dict]) -> dict[str, str]:
    """After a phase completes, read the files it wrote from disk.

    Parameters
    ----------
    workspace:
        Root directory of the target project.
    phase_tasks:
        Tasks that were in this phase.

    Returns
    -------
    dict[str, str]
        ``{relative_path: file_content}`` for all files that exist on disk.
    """
    base = Path(workspace)
    output: dict[str, str] = {}

    for task in phase_tasks:
        all_files = list(task.get("files_to_create", []) or [])
        all_files += list(task.get("files_to_modify", []) or [])

        for fpath in all_files:
            if not fpath:
                continue
            full_path = base / fpath
            if full_path.exists() and full_path.is_file():
                try:
                    content = full_path.read_text(encoding="utf-8", errors="replace")
                    output[fpath] = content
                except OSError:
                    pass

    return output


# ---------------------------------------------------------------------------
# Phase output validation
# ---------------------------------------------------------------------------


def validate_phase_output(
    workspace: str,
    phase_tasks: list[dict],
    design_spec: dict,
) -> list[str]:
    """Check that phase output matches the architecture contract.

    Validates:
    - Files exist at the paths specified in architecture
    - Key classes/functions are present (basic import check)
    - No files written to wrong locations (unexpected directories)

    Parameters
    ----------
    workspace:
        Root directory of the target project.
    phase_tasks:
        Tasks that were in this phase.
    design_spec:
        Full architecture design spec.

    Returns
    -------
    list[str]
        List of violation descriptions. Empty means all good.
    """
    base = Path(workspace)
    violations: list[str] = []

    for task in phase_tasks:
        task_id = task.get("id", "unknown")

        # Check files_to_create actually exist
        for fpath in task.get("files_to_create", []) or []:
            if not fpath:
                continue
            full_path = base / fpath
            if not full_path.exists():
                violations.append(
                    f"[{task_id}] Missing file: {fpath} (expected by architecture)"
                )
            elif full_path.stat().st_size == 0:
                violations.append(
                    f"[{task_id}] Empty file: {fpath} (0 bytes)"
                )
            elif fpath.endswith(".py"):
                # Basic content check for Python files
                try:
                    content = full_path.read_text(encoding="utf-8", errors="replace")
                    if content.strip() in ("", "pass", "# TODO"):
                        violations.append(
                            f"[{task_id}] Stub-only file: {fpath} (no real implementation)"
                        )
                except OSError:
                    violations.append(
                        f"[{task_id}] Unreadable file: {fpath}"
                    )

    # Cross-check against architecture component file lists
    components = design_spec.get("components", [])
    arch_files: set[str] = set()
    for comp in components:
        for f in comp.get("files", []):
            arch_files.add(f)

    if arch_files:
        # Check that tasks reference architecture-defined paths
        for task in phase_tasks:
            for fpath in task.get("files_to_create", []) or []:
                if fpath and arch_files and fpath not in arch_files:
                    # Only warn if architecture has file lists -- not all do
                    violations.append(
                        f"[{task.get('id', '?')}] File {fpath} not in architecture spec "
                        f"(expected one of: {', '.join(sorted(arch_files)[:5])}...)"
                    )

    return violations


# ---------------------------------------------------------------------------
# MANIFEST.yaml update
# ---------------------------------------------------------------------------


def update_manifest(
    manifest_path: str,
    stage: int,
    status: str,
    details: dict | None = None,
) -> None:
    """Update MANIFEST.yaml with stage completion status.

    Creates the file if it doesn't exist. Updates the ``stages`` list entry
    for the given stage number with the provided status and details.

    Parameters
    ----------
    manifest_path:
        Path to the MANIFEST.yaml file.
    stage:
        Stage number (0-9, matching Pineapple Pipeline stages).
    status:
        Status string (e.g. ``"completed"``, ``"failed"``, ``"in_progress"``).
    details:
        Optional dict of extra details to merge into the stage entry.
    """
    try:
        import yaml
    except ImportError:
        print("[Orchestrator] PyYAML not available, skipping MANIFEST update")
        return

    path = Path(manifest_path)

    # Load existing or create new
    manifest: dict[str, Any] = {}
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as fh:
                manifest = yaml.safe_load(fh) or {}
        except (yaml.YAMLError, OSError) as exc:
            print(f"[Orchestrator] Could not read MANIFEST: {exc}")
            manifest = {}

    # Ensure stages list exists
    if "stages" not in manifest or not isinstance(manifest["stages"], list):
        manifest["stages"] = []

    # Pad stages list to reach the target index
    while len(manifest["stages"]) <= stage:
        manifest["stages"].append({"stage": len(manifest["stages"]), "status": "pending"})

    # Update the target stage
    entry = manifest["stages"][stage]
    entry["status"] = status
    entry["updated_at"] = datetime.now(timezone.utc).isoformat()
    if details:
        entry.update(details)

    # Write back
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            yaml.dump(manifest, fh, default_flow_style=False, sort_keys=False)
    except OSError as exc:
        print(f"[Orchestrator] Could not write MANIFEST: {exc}")


# ---------------------------------------------------------------------------
# Read existing files from workspace
# ---------------------------------------------------------------------------


def _read_existing_files(workspace: str, tasks: list[dict]) -> dict[str, str]:
    """Read files from the workspace that tasks reference in files_to_modify.

    Only reads files that already exist -- these are files the builder will
    need to extend rather than create from scratch.
    """
    base = Path(workspace)
    existing: dict[str, str] = {}

    for task in tasks:
        for fpath in task.get("files_to_modify", []) or []:
            if not fpath or fpath in existing:
                continue
            full_path = base / fpath
            if full_path.exists() and full_path.is_file():
                try:
                    existing[fpath] = full_path.read_text(
                        encoding="utf-8", errors="replace"
                    )
                except OSError:
                    pass

    return existing


# ---------------------------------------------------------------------------
# Workspace manifest (structured context for agents)
# ---------------------------------------------------------------------------


def build_workspace_manifest(
    task: dict,
    design_spec: dict,
    existing_files: list[str],
    prior_phase_files: dict[str, str],
) -> str:
    """Generate structured workspace awareness text for an agent.

    Instead of the agent blindly calling list_files('.'), this tells it:
    - What directories exist
    - Which files THIS task should CREATE (from architecture)
    - Which files THIS task should MODIFY (from architecture)
    - Key imports available from prior phases
    - Test file locations

    Parameters
    ----------
    task:
        The task dict being built.
    design_spec:
        Full architecture design spec dict.
    existing_files:
        List of relative file paths already on disk.
    prior_phase_files:
        ``{path: content}`` from prior completed phases.

    Returns
    -------
    str
        Structured workspace description block.
    """
    parts: list[str] = []

    # 1. Directory tree from existing files
    dirs: set[str] = set()
    for fpath in existing_files:
        parent = str(Path(fpath).parent)
        while parent and parent != ".":
            dirs.add(parent)
            parent = str(Path(parent).parent)
    for fpath in prior_phase_files:
        parent = str(Path(fpath).parent)
        while parent and parent != ".":
            dirs.add(parent)
            parent = str(Path(parent).parent)

    if dirs:
        parts.append("=== WORKSPACE DIRECTORIES ===")
        for d in sorted(dirs):
            parts.append(f"  {d}/")
        parts.append("")

    # 2. Files this task should CREATE
    files_to_create = task.get("files_to_create", []) or []
    if files_to_create:
        parts.append("=== FILES TO CREATE (this task) ===")
        for fpath in files_to_create:
            parts.append(f"  CREATE: {fpath}")
        parts.append("")

    # 3. Files this task should MODIFY
    files_to_modify = task.get("files_to_modify", []) or []
    if files_to_modify:
        parts.append("=== FILES TO MODIFY (this task) ===")
        for fpath in files_to_modify:
            exists = fpath in existing_files or fpath in prior_phase_files
            status = "exists" if exists else "NOT YET ON DISK"
            parts.append(f"  MODIFY: {fpath} ({status})")
        parts.append("")

    # 4. Key imports from prior phases
    if prior_phase_files:
        parts.append("=== AVAILABLE FROM PRIOR PHASES ===")
        for fpath, content in prior_phase_files.items():
            if not fpath.endswith(".py"):
                parts.append(f"  {fpath}")
                continue
            # Extract top-level class and function names for import hints
            imports: list[str] = []
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("class ") and "(" in stripped:
                    name = stripped.split("class ")[1].split("(")[0].strip()
                    imports.append(name)
                elif stripped.startswith("def ") and "(" in stripped:
                    name = stripped.split("def ")[1].split("(")[0].strip()
                    if not name.startswith("_"):
                        imports.append(name)
            if imports:
                parts.append(f"  {fpath}  ->  exports: {', '.join(imports[:10])}")
            else:
                parts.append(f"  {fpath}")
        parts.append("")

    # 5. Test file locations from architecture
    components = design_spec.get("components", [])
    test_files: list[str] = []
    for comp in components:
        for f in comp.get("files", []):
            if "test" in f.lower() or f.startswith("tests/"):
                test_files.append(f)
    if test_files:
        parts.append("=== TEST FILES (from architecture) ===")
        for tf in test_files:
            parts.append(f"  {tf}")
        parts.append("")

    if not parts:
        return ""

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Main entry point: run_phased_build
# ---------------------------------------------------------------------------


def run_phased_build(
    tasks: list,
    workspace: str,
    design_spec: dict,
    state: dict,
    build_fn: Callable,
    process_fn: Callable,
    max_concurrent: int = 4,
) -> tuple[list[dict], float]:
    """Run tasks in dependency phases. Returns (build_results, total_cost).

    This is the functional, callback-based orchestrator that integrates with
    builder_node. Instead of trying to build code itself, it delegates to
    the provided ``build_fn`` and ``process_fn`` callbacks.

    Flow:
    1. Extract phases from architecture
    2. Map tasks to phases
    3. For each phase sequentially:
       a. Enrich each task with context (architecture spec + prior phase code + existing files)
       b. Execute phase tasks (parallel within phase, limited by semaphore)
       c. Process results (write files, commit) via process_fn
       d. Collect output files from disk
       e. Validate against architecture
       f. Print phase summary
    4. Return all build results + total cost

    Parameters
    ----------
    tasks:
        List of Task objects from TaskPlan.
    workspace:
        Path to the project worktree.
    design_spec:
        Architecture design spec dict (includes ``_raw_document``).
    state:
        Full pipeline state dict (for extracting retry feedback, workspace_info, etc).
    build_fn:
        ``_build_one_task(task, workspace, design_summary, cumulative_files,
        review_result, verify_record, run_files, workspace_info, use_llm, llm,
        builder_mode, design_spec)`` -- returns ``(BuildResult, cost)``.
    process_fn:
        ``_process_build_result(result, workspace, run_files, cumulative_files,
        workspace_info)`` -- writes files to disk, commits, returns file count.
    max_concurrent:
        Max parallel tasks within a single phase.

    Returns
    -------
    tuple[list[dict], float]
        ``(build_results, total_cost)`` where build_results are
        BuildResult-compatible dicts.
    """
    from pineapple.models import FileWrite  # local import to avoid circular

    print("[Orchestrator] Starting phase-based build orchestration")

    # Normalize tasks to dicts for phase mapping, keep originals for build_fn
    task_dicts: list[dict] = []
    task_by_id: dict[str, Any] = {}
    for t in tasks:
        if hasattr(t, "model_dump"):
            td = t.model_dump()
        elif isinstance(t, dict):
            td = t
        else:
            td = dict(t)
        task_dicts.append(td)
        task_by_id[td.get("id", "")] = t  # original Task object

    if not task_dicts:
        print("[Orchestrator] No tasks found, nothing to orchestrate")
        return [], 0.0

    # --- Step 1: Extract phases from architecture ---
    phases = extract_phases_from_architecture(design_spec)
    if not phases:
        print("[Orchestrator] No phase info in architecture, using single-phase fallback")
        phases = [["ALL"]]

    print(f"[Orchestrator] Found {len(phases)} build phases:")
    for i, phase_ids in enumerate(phases):
        print(f"  Phase {i + 1}: {', '.join(phase_ids)}")

    # --- Step 2: Map tasks to phases ---
    phased_task_dicts = map_tasks_to_phases(task_dicts, phases)

    for i, phase_task_list in enumerate(phased_task_dicts):
        task_ids = [t.get("id", "?") for t in phase_task_list]
        print(f"  Phase {i + 1} tasks: {', '.join(task_ids) if task_ids else '(empty)'}")

    # --- Prepare shared state for build_fn callbacks ---
    existing_files = _read_existing_files(workspace, task_dicts)
    if existing_files:
        print(f"[Orchestrator] Read {len(existing_files)} existing files from workspace")

    design_summary = design_spec.get("summary", "No design spec available.")
    workspace_info = state.get("workspace_info") or {}

    # Determine LLM availability (same logic as builder_node)
    try:
        from pineapple.llm import get_llm_client, has_any_llm_key
        use_llm = has_any_llm_key()
        llm = get_llm_client(stage="build") if use_llm else None
    except ImportError:
        use_llm = False
        llm = None

    import os
    builder_mode = os.environ.get("PINEAPPLE_BUILDER", "single_shot")

    # Retry feedback from prior attempts
    attempt_counts = state.get("attempt_counts", {})
    review_result: dict = {}
    verify_record: dict = {}
    if attempt_counts.get("build", 0) > 0:
        review_result = state.get("review_result") or {}
        verify_record = state.get("verify_record") or {}

    # Shared mutable state (thread-safe via semaphore and lock)
    completed_code: dict[str, str] = {}  # accumulates across phases
    cumulative_files: list[FileWrite] = []
    run_files: set[str] = set()

    # Seed run_files from previous attempts for retry overwrite
    if attempt_counts.get("build", 0) > 0:
        for prev in state.get("build_results", []):
            for fw in prev.get("files_written", []):
                path = fw.get("path", "") if isinstance(fw, dict) else getattr(fw, "path", "")
                if path:
                    run_files.add(path)

    build_results: list[dict] = []
    total_cost = 0.0
    total_violations = 0
    results_lock = threading.Lock()

    manifest_path = state.get("_manifest_path")
    if manifest_path:
        update_manifest(manifest_path, 5, "in_progress", {
            "phase_count": len(phases),
            "task_count": len(task_dicts),
        })

    # --- Step 3: Execute phases sequentially ---
    for phase_idx, phase_task_list in enumerate(phased_task_dicts):
        phase_num = phase_idx + 1
        print(f"\n[Orchestrator] === Phase {phase_num}/{len(phased_task_dicts)} ===")

        if not phase_task_list:
            print(f"[Orchestrator] Phase {phase_num}: no tasks, skipping")
            continue

        # 3a. Enrich each task with orchestrator context
        for task_dict in phase_task_list:
            context = build_task_context(
                task=task_dict,
                design_spec=design_spec,
                completed_code=completed_code,
                existing_files=existing_files,
            )
            # Also build workspace manifest
            ws_manifest = build_workspace_manifest(
                task=task_dict,
                design_spec=design_spec,
                existing_files=list(existing_files.keys()),
                prior_phase_files=completed_code,
            )
            combined_context = ""
            if ws_manifest:
                combined_context += ws_manifest + "\n"
            if context:
                combined_context += context
            task_dict["_orchestrator_context"] = combined_context
            if combined_context:
                print(f"  [{task_dict.get('id', '?')}] Injected {len(combined_context)} chars of context")

        # 3b. Execute phase tasks (parallel within phase)
        semaphore = threading.Semaphore(max_concurrent)
        phase_results: list[tuple[dict, float]] = []  # (result_dict, cost)

        # Only the last phase runs tests — earlier phases have unresolvable imports
        is_last_phase = (phase_idx == len(phased_task_dicts) - 1)

        def _run_task(task_dict: dict) -> tuple[dict, float, int]:
            """Execute a single task behind the semaphore."""
            tid = task_dict.get("id", "?")
            original_task = task_by_id.get(tid)
            if original_task is None:
                # Reconstruct Task from dict if original not found
                from pineapple.models import Task
                original_task = Task(**task_dict)

            # Inject orchestrator context as prior_context by patching
            # cumulative_files with a synthetic entry containing the context
            orchestrator_ctx = task_dict.get("_orchestrator_context", "")
            extra_files: list[FileWrite] = []
            if orchestrator_ctx:
                extra_files = [FileWrite(
                    path="__orchestrator_context__.md",
                    content=orchestrator_ctx,
                )]

            with semaphore:
                result, cost = build_fn(
                    original_task, workspace, design_summary,
                    extra_files + list(cumulative_files),
                    review_result, verify_record, run_files,
                    workspace_info, use_llm, llm, builder_mode,
                    design_spec,
                    skip_tests=not is_last_phase,
                )
                # Process: write files to disk and commit
                files_count = process_fn(
                    result, workspace, run_files,
                    cumulative_files, workspace_info,
                )
                result_dict = result.model_dump() if hasattr(result, "model_dump") else result
                return result_dict, cost, files_count

        if len(phase_task_list) == 1:
            # Single task -- run directly (no thread overhead)
            rd, cost, fc = _run_task(phase_task_list[0])
            with results_lock:
                build_results.append(rd)
                total_cost += cost
        else:
            # Parallel execution within phase
            print(f"  [Orchestrator] Running {len(phase_task_list)} tasks in parallel (max {max_concurrent})")
            with ThreadPoolExecutor(max_workers=min(len(phase_task_list), max_concurrent)) as executor:
                futures = {}
                for task_dict in phase_task_list:
                    future = executor.submit(_run_task, task_dict)
                    futures[future] = task_dict

                for future in as_completed(futures):
                    task_dict = futures[future]
                    tid = task_dict.get("id", "?")
                    try:
                        rd, cost, fc = future.result()
                        with results_lock:
                            build_results.append(rd)
                            total_cost += cost
                    except Exception as e:
                        print(f"    ERROR: Task {tid}: {e}")
                        from pineapple.models import BuildResult
                        err = BuildResult(
                            task_id=tid,
                            status="failed",
                            commits=[],
                            errors=[str(e)],
                        )
                        with results_lock:
                            build_results.append(err.model_dump())

        # 3d. Collect output files from disk (after all phase tasks complete)
        new_code = collect_phase_output(workspace, phase_task_list)
        completed_code.update(new_code)
        print(f"  Phase {phase_num}: collected {len(new_code)} files from disk")

        # 3e. Validate against architecture
        violations = validate_phase_output(workspace, phase_task_list, design_spec)
        total_violations += len(violations)
        if violations:
            print(f"  Phase {phase_num}: {len(violations)} violation(s):")
            for v in violations:
                print(f"    - {v}")
        else:
            print(f"  Phase {phase_num}: all validations passed")

        # 3f. Update manifest after each phase
        if manifest_path:
            update_manifest(manifest_path, 5, "in_progress", {
                "current_phase": phase_num,
                "phases_completed": phase_num,
                "phases_total": len(phased_task_dicts),
                "violations_so_far": total_violations,
            })

    # Final manifest update
    if manifest_path:
        final_status = "completed" if total_violations == 0 else "completed_with_warnings"
        update_manifest(manifest_path, 5, final_status, {
            "phases_completed": len(phased_task_dicts),
            "phases_total": len(phased_task_dicts),
            "total_violations": total_violations,
            "files_produced": len(completed_code),
        })

    completed = sum(1 for r in build_results if r.get("status") == "completed")
    failed = sum(1 for r in build_results if r.get("status") == "failed")
    print(f"\n[Orchestrator] Build orchestration complete:")
    print(f"  Phases: {len(phased_task_dicts)}")
    print(f"  Tasks: {completed} completed, {failed} failed")
    print(f"  Total files: {len(completed_code)}")
    print(f"  Total violations: {total_violations}")
    print(f"  Total cost: ${total_cost:.4f}")

    return build_results, total_cost
