"""Load a MANIFEST.yaml and its artifacts into PipelineState for resume."""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

import yaml

from pineapple.state import PipelineStage, PipelineState

# ---------------------------------------------------------------------------
# Stage-number → PipelineStage enum mapping
# ---------------------------------------------------------------------------

_STAGE_MAP: dict[int, str] = {
    0: PipelineStage.INTAKE,
    1: PipelineStage.STRATEGIC_REVIEW,
    2: PipelineStage.ARCHITECTURE,
    3: PipelineStage.PLAN,
    4: PipelineStage.SETUP,
    5: PipelineStage.BUILD,
    6: PipelineStage.VERIFY,
    7: PipelineStage.REVIEW,
    8: PipelineStage.SHIP,
    9: PipelineStage.EVOLVE,
}


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def load_manifest(manifest_path: str, target_dir: str | None = None) -> dict[str, Any]:
    """Read MANIFEST.yaml and return the parsed dict.

    Parameters
    ----------
    manifest_path:
        Absolute or relative path to the MANIFEST.yaml file.
    target_dir:
        Optional base directory.  When provided, a relative *manifest_path*
        is resolved against it.  Has no effect when *manifest_path* is
        already absolute.

    Returns
    -------
    dict
        Raw YAML content as a Python dict.

    Raises
    ------
    FileNotFoundError
        If the MANIFEST.yaml does not exist at the resolved path.
    yaml.YAMLError
        If the file is not valid YAML.
    """
    path = Path(manifest_path)
    if not path.is_absolute() and target_dir:
        path = Path(target_dir) / path

    if not path.exists():
        raise FileNotFoundError(f"MANIFEST not found: {path}")

    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_artifact(artifact_path: str, base_dir: str) -> str:
    """Read a stage artifact file and return its content as a string.

    Parameters
    ----------
    artifact_path:
        Path to the artifact (may be relative to *base_dir*).
    base_dir:
        Directory used to resolve relative *artifact_path* values.

    Returns
    -------
    str
        Raw text content of the file.

    Raises
    ------
    FileNotFoundError
        If the artifact does not exist.
    """
    path = Path(artifact_path)
    if not path.is_absolute():
        path = Path(base_dir) / artifact_path

    if not path.exists():
        raise FileNotFoundError(f"Artifact not found: {path}")

    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Markdown section extraction
# ---------------------------------------------------------------------------


def _extract_sections(markdown: str) -> dict[str, str]:
    """Split markdown into a dict of {lowercase_header: section_body}.

    Only level-2 headers (``##``) are used as section boundaries.  The
    returned bodies include everything between the opening header and the
    next ``##`` header (exclusive).
    """
    sections: dict[str, str] = {}
    current_key: str | None = None
    current_lines: list[str] = []

    for line in markdown.splitlines():
        m = re.match(r"^##\s+(.+)", line)
        if m:
            if current_key is not None:
                sections[current_key] = "\n".join(current_lines).strip()
            current_key = m.group(1).strip().lower()
            current_lines = []
        else:
            if current_key is not None:
                current_lines.append(line)

    if current_key is not None:
        sections[current_key] = "\n".join(current_lines).strip()

    return sections


def _bullet_lines(text: str) -> list[str]:
    """Extract non-empty lines from a block of text as a list of strings.

    Leading ``- ``, ``* ``, and ``+ `` bullet markers are stripped.
    Blank lines and markdown table rows (``|``) are skipped.
    """
    result: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("|") or line.startswith("---"):
            continue
        # Strip common bullet markers
        line = re.sub(r"^[-*+]\s+", "", line)
        # Strip bold / checkbox markdown
        line = re.sub(r"^\[[ xX]\]\s*", "", line)
        line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
        result.append(line)
    return result


def _first_paragraph(text: str) -> str:
    """Return the first non-empty paragraph of *text*."""
    para_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            para_lines.append(stripped)
        elif para_lines:
            break
    return " ".join(para_lines)


# ---------------------------------------------------------------------------
# Strategic brief extraction
# ---------------------------------------------------------------------------

# Maps field names → possible markdown section header keywords
_BRIEF_SECTION_HINTS: dict[str, list[str]] = {
    "what":            ["vision statement", "what we're building", "what we are building"],
    "why":             ["why restart", "why now", "problem statement", "context & problem"],
    "not_building":    ["out of scope", "not building"],
    "who_benefits":    ["who benefits", "stakeholders"],
    "assumptions":     ["assumptions", "clarify unknowns"],
    "open_questions":  ["open questions", "unknowns"],
}


def _parse_strategic_brief(markdown: str) -> dict[str, Any]:
    """Extract structured fields from a strategic brief markdown document.

    The parser is tolerant of varying header styles (Roman numerals, plain
    English, numbered).  When a dedicated section cannot be located it falls
    back to full-document pattern searches so that common patterns such as
    blockquote vision statements and "OUT OF SCOPE" bullet lists are always
    captured.
    """
    sections = _extract_sections(markdown)
    # Also split on H3 (###) headers so sub-sections are accessible
    h3_sections: dict[str, str] = {}
    current_key: str | None = None
    current_lines: list[str] = []
    for line in markdown.splitlines():
        m = re.match(r"^###\s+(.+)", line)
        if m:
            if current_key is not None:
                h3_sections[current_key] = "\n".join(current_lines).strip()
            current_key = m.group(1).strip().lower()
            current_lines = []
        else:
            if current_key is not None:
                current_lines.append(line)
    if current_key is not None:
        h3_sections[current_key] = "\n".join(current_lines).strip()

    def find_section(*hints: str) -> str:
        """Return first section body whose key contains any hint (substring).

        Searches H2 sections first, then H3 sub-sections.
        """
        for hint in hints:
            for key, body in sections.items():
                if hint in key:
                    return body
            for key, body in h3_sections.items():
                if hint in key:
                    return body
        return ""

    # -- what ---------------------------------------------------------------
    # Priority: blockquote anywhere in the doc, then vision/what section text
    bq_match = re.search(r"^>\s*\*?\*?(.+?)\*?\*?$", markdown, re.MULTILINE)
    if bq_match:
        what = bq_match.group(1).strip()
        # Strip residual inline bold markers
        what = re.sub(r"\*\*(.+?)\*\*", r"\1", what)
    else:
        what_text = find_section(*_BRIEF_SECTION_HINTS["what"])
        what = _first_paragraph(what_text)

    # -- why ----------------------------------------------------------------
    why_text = find_section(*_BRIEF_SECTION_HINTS["why"])
    why = _first_paragraph(why_text) or why_text[:300].strip()

    # -- not_building -------------------------------------------------------
    # Look for an "OUT OF SCOPE" or "NOT BUILDING" block anywhere in the doc
    not_building: list[str] = []
    scope_match = re.search(
        r"\*\*OUT OF SCOPE.*?\*\*.*?:(.*?)(?=\n\n\*\*|\n##|\Z)",
        markdown, re.DOTALL | re.IGNORECASE
    )
    if scope_match:
        not_building = _bullet_lines(scope_match.group(1))
    else:
        nb_text = find_section(*_BRIEF_SECTION_HINTS["not_building"])
        not_building = _bullet_lines(nb_text)

    # -- who_benefits -------------------------------------------------------
    who_text = find_section(*_BRIEF_SECTION_HINTS["who_benefits"])
    who_benefits = (
        _first_paragraph(who_text)
        if who_text
        else "Engineering teams using the Claude terminal workflow"
    )

    # -- assumptions --------------------------------------------------------
    # Pineapple briefs often embed assumptions inside "Clarify Unknowns" sub-
    # sections, formatted as Q&A with "**Assumption:**" lines.
    assumptions: list[str] = []
    assumption_matches = re.findall(
        r"\*\*Assumption[:\*]*\s*(.+?)(?=\n|\Z)", markdown, re.IGNORECASE
    )
    if assumption_matches:
        assumptions = [a.strip() for a in assumption_matches if a.strip()]
    else:
        assumptions_text = find_section(*_BRIEF_SECTION_HINTS["assumptions"])
        assumptions = _bullet_lines(assumptions_text)

    # -- open_questions -------------------------------------------------------
    oq_text = find_section(*_BRIEF_SECTION_HINTS["open_questions"])
    # If the section is actually a Q&A block, grab the numbered questions
    open_questions: list[str] = []
    if oq_text:
        for line in oq_text.splitlines():
            stripped = line.strip()
            if re.match(r"^\d+\.", stripped):
                # Numbered question — strip "**..." bold markers
                q = re.sub(r"\*\*(.+?)\*\*", r"\1", stripped)
                q = re.sub(r"^\d+\.\s*", "", q)
                open_questions.append(q)
        if not open_questions:
            open_questions = _bullet_lines(oq_text)

    return {
        "what": what,
        "why": why,
        "not_building": not_building,
        "who_benefits": who_benefits,
        "assumptions": assumptions,
        "open_questions": open_questions,
        "approved": False,
    }


# ---------------------------------------------------------------------------
# Architecture / design-spec extraction
# ---------------------------------------------------------------------------


def _parse_design_spec(markdown: str) -> dict[str, Any]:
    """Extract structured fields from an architecture design markdown document."""
    sections = _extract_sections(markdown)

    # -- title / summary ----------------------------------------------------
    # Use the first H1 line as the title; fall back to a generic label
    h1_match = re.search(r"^#\s+(.+)", markdown, re.MULTILINE)
    title = h1_match.group(1).strip() if h1_match else "Architecture Design"

    # Summary: first non-empty paragraph of the document (before any ##)
    preamble = markdown.split("##")[0]
    summary = _first_paragraph(preamble) or title

    # -- components ---------------------------------------------------------
    components: list[dict[str, Any]] = []

    # Each SC-XX section is a component spec.  Detect "### SC-XX: Name" headers.
    sc_pattern = re.compile(
        r"###\s+(SC-\d+):\s+(.+?)\n(.*?)(?=\n###|\Z)", re.DOTALL
    )
    for m in sc_pattern.finditer(markdown):
        sc_id = m.group(1).strip()
        sc_name = m.group(2).strip()
        sc_body = m.group(3)

        # Collect "Files to create/modify" bullet items
        files: list[str] = []
        for line in sc_body.splitlines():
            stripped = line.strip()
            if stripped.startswith("`") and stripped.endswith("`"):
                candidate = stripped.strip("`")
                if "/" in candidate or candidate.endswith(".py") or candidate.endswith(".ts"):
                    files.append(candidate)
            elif re.match(r"^[-*+]\s+`(.+)`", stripped):
                inner = re.match(r"^[-*+]\s+`(.+)`", stripped).group(1)
                if "/" in inner or inner.endswith(".py") or inner.endswith(".ts"):
                    files.append(inner)

        # Extract libraries mentioned (e.g., "CadQueryEngine", "vlad.py")
        libs: list[str] = []
        lib_match = re.search(r"[Dd]ependencies.*?:(.*?)(?:\n\n|\Z)", sc_body, re.DOTALL)
        if lib_match:
            libs = _bullet_lines(lib_match.group(1))

        # Short description: first sentence / paragraph of the SC body
        purpose_match = re.search(r"\*\*Purpose\*\*\s*:\s*(.+)", sc_body)
        description = purpose_match.group(1).strip() if purpose_match else sc_name

        components.append({
            "name": f"{sc_id}: {sc_name}",
            "description": description,
            "files": files,
            "libraries": libs,
        })

    # -- technology_choices_list --------------------------------------------
    tech_choices: list[dict[str, str]] = []

    # Look for "What already exists" or explicit tech tables
    existing_section = ""
    for key, body in sections.items():
        if "already exists" in key or "technology" in key or "stack" in key:
            existing_section = body
            break

    # Parse inline backtick items that suggest technology choices
    tech_patterns = [
        (r"CadQuery", "Geometry Engine", "CadQuery (BREP)"),
        (r"SQLite", "Database", "SQLite (aiosqlite)"),
        (r"FastAPI", "Backend Framework", "FastAPI"),
        (r"Three\.js|ThreeJS", "3D Renderer", "Three.js"),
        (r"pytest", "Test Framework", "pytest"),
        (r"TypeScript", "Frontend Language", "TypeScript"),
        (r"React|R3F", "Frontend Framework", "React / R3F"),
        (r"VLAD|vlad\.py", "Geometry Validator", "VLAD (vlad.py)"),
        (r"MCP", "Tool Protocol", "MCP (Model Context Protocol)"),
    ]
    for pattern, category, choice in tech_patterns:
        if re.search(pattern, markdown, re.IGNORECASE):
            tech_choices.append({"category": category, "choice": choice})

    spec: dict[str, Any] = {
        "title": title,
        "summary": summary,
        "components": components,
        "technology_choices_list": tech_choices,
        "approved": False,
    }
    # _raw_document is injected by callers that pass raw_md — see build_state_from_manifest
    return spec


# ---------------------------------------------------------------------------
# Context bundle construction
# ---------------------------------------------------------------------------


def _build_context_bundle(manifest: dict[str, Any]) -> dict[str, Any]:
    """Build a context_bundle dict from manifest ADRs and success criteria."""

    # project_type: derive from project name or default
    project_name: str = manifest.get("project", "unknown")
    project_type = "web-app" if any(
        kw in project_name.lower() for kw in ("studio", "forge", "app", "web")
    ) else "generic"

    # codebase_summary: success criteria as structured dict
    sc_list = manifest.get("success_criteria", [])
    codebase_summary: dict[str, Any] = {
        "success_criteria": [
            {
                "id": sc.get("id", ""),
                "title": sc.get("title", ""),
                "status": sc.get("status", "pending"),
                "verify_command": sc.get("verify_command", ""),
            }
            for sc in sc_list
        ],
        "total_criteria": len(sc_list),
        "completed_criteria": sum(1 for sc in sc_list if sc.get("status") == "completed"),
    }

    # Include git metadata if present
    git_info = manifest.get("git", {})
    if git_info:
        codebase_summary["git_branch"] = git_info.get("branch", "")
        codebase_summary["git_base"] = git_info.get("base", "")

    # project_memory: locked ADRs
    adr_list = manifest.get("adr", [])
    project_memory: dict[str, Any] = {
        "locked_adrs": [
            {
                "id": adr.get("id", ""),
                "decision": adr.get("decision", ""),
                "status": adr.get("status", ""),
                "locked_since": adr.get("locked_since", ""),
            }
            for adr in adr_list
        ],
        "total_adrs": len(adr_list),
        "manifest_version": manifest.get("version", "1.0"),
        "manifest_created": manifest.get("created", ""),
    }

    return {
        "project_type": project_type,
        "context_files": [],
        "classification": "resume",
        "codebase_summary": codebase_summary,
        "project_memory": project_memory,
        "similar_projects": [],
    }


# ---------------------------------------------------------------------------
# Locate well-known artifact files
# ---------------------------------------------------------------------------


def _find_brief_path(manifest: dict[str, Any], base_dir: str) -> str | None:
    """Locate the strategic brief artifact path from the manifest.

    Search order:
    1. ``documents.strategic_brief.file`` key (only when file exists on disk)
    2. ``stages[stage==1].artifact`` key (only when file exists on disk)
    3. Convention: ``.pineapple/<run>/00-strategic-brief.md`` (auto-detected)
    """
    def _exists(rel: str) -> bool:
        p = Path(rel)
        return p.exists() if p.is_absolute() else (Path(base_dir) / rel).exists()

    docs = manifest.get("documents", {})
    brief_doc = docs.get("strategic_brief", {})
    if isinstance(brief_doc, dict) and brief_doc.get("file"):
        candidate = brief_doc["file"]
        if _exists(candidate):
            return candidate

    stages = manifest.get("stages", [])
    for stage in stages:
        if stage.get("stage") == 1 and stage.get("artifact"):
            candidate = stage["artifact"]
            if _exists(candidate):
                return candidate

    # Convention search: look for 00-*.md in .pineapple subdirectories
    pineapple_dir = Path(base_dir) / ".pineapple"
    if pineapple_dir.exists():
        candidates = sorted(pineapple_dir.rglob("00-*.md"))
        if candidates:
            return str(candidates[0].relative_to(base_dir))

    return None


def _find_arch_path(manifest: dict[str, Any], base_dir: str) -> str | None:
    """Locate the architecture design artifact path from the manifest.

    Search order:
    1. ``stages[stage==2].artifact`` key (only when file exists on disk)
    2. ``documents.implementation_plan.file`` key (only when file exists on disk)
    3. Convention: ``.pineapple/<run>/01-*.md``
    """
    def _exists(rel: str) -> bool:
        p = Path(rel)
        return p.exists() if p.is_absolute() else (Path(base_dir) / rel).exists()

    stages = manifest.get("stages", [])
    for stage in stages:
        if stage.get("stage") == 2 and stage.get("artifact"):
            candidate = stage["artifact"]
            if _exists(candidate):
                return candidate

    docs = manifest.get("documents", {})
    impl_doc = docs.get("implementation_plan", {})
    if isinstance(impl_doc, dict) and impl_doc.get("file") and impl_doc.get("status") != "pending":
        candidate = impl_doc["file"]
        if _exists(candidate):
            return candidate

    # Convention search
    pineapple_dir = Path(base_dir) / ".pineapple"
    if pineapple_dir.exists():
        candidates = sorted(pineapple_dir.rglob("01-*.md"))
        if candidates:
            return str(candidates[0].relative_to(base_dir))

    return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def build_state_from_manifest(
    manifest_path: str,
    resume_from: int = 0,
    request: str = "",
    target_dir: str | None = None,
    run_id: str = "",
    project_name: str = "",
    path: str = "",
) -> PipelineState:
    """Build a PipelineState dict populated from MANIFEST + artifacts.

    Parameters
    ----------
    manifest_path:
        Path to the MANIFEST.yaml file.
    resume_from:
        Integer stage number to resume from (0-9).
    request:
        Optional override for the ``request`` field.  When empty, a
        descriptive string is generated from the manifest project name.
    target_dir:
        Base directory for resolving relative artifact paths.  Defaults to
        the directory containing *manifest_path*.

    Returns
    -------
    PipelineState
        A fully populated TypedDict ready to hand to the LangGraph pipeline.
    """
    # Resolve base_dir from manifest location when target_dir not given
    manifest_abs = Path(manifest_path)
    if not manifest_abs.is_absolute() and target_dir:
        manifest_abs = Path(target_dir) / manifest_path
    base_dir: str = target_dir or str(manifest_abs.parent)

    # --- 1. Load MANIFEST ---------------------------------------------------
    manifest = load_manifest(str(manifest_abs))

    manifest_project: str = manifest.get("project", "unknown-project")
    if not project_name:
        project_name = manifest_project
    if not request:
        request = f"Resume {project_name} from stage {resume_from}"

    # --- 2. Determine target PipelineStage ----------------------------------
    if resume_from not in _STAGE_MAP:
        raise ValueError(
            f"resume_from must be 0-9, got {resume_from}. "
            f"Valid stages: {sorted(_STAGE_MAP)}"
        )
    current_stage: str = _STAGE_MAP[resume_from].value

    # --- 3. Build context_bundle from manifest metadata ---------------------
    context_bundle = _build_context_bundle(manifest)

    # --- 4. Load strategic brief (Stage 1 artifact) -------------------------
    strategic_brief: dict[str, Any] | None = None
    if resume_from > 1:
        brief_rel = _find_brief_path(manifest, base_dir)
        if brief_rel:
            try:
                brief_md = load_artifact(brief_rel, base_dir)
                strategic_brief = _parse_strategic_brief(brief_md)
            except FileNotFoundError:
                # Artifact reference exists but file is missing — build a
                # minimal placeholder so the pipeline has something to work from
                strategic_brief = {
                    "what": f"Resume {project_name}",
                    "why": "Resuming from manifest",
                    "not_building": [],
                    "who_benefits": "Project team",
                    "assumptions": [],
                    "open_questions": [],
                    "approved": True,
                }

    # --- 5. Load architecture design (Stage 2 artifact) ---------------------
    design_spec: dict[str, Any] | None = None
    if resume_from > 2:
        arch_rel = _find_arch_path(manifest, base_dir)
        if arch_rel:
            try:
                arch_md = load_artifact(arch_rel, base_dir)
                design_spec = _parse_design_spec(arch_md)
                # Store the full raw markdown so the planner prompt receives
                # the complete architecture document (file inventory, API
                # endpoints, build phases, risk assessment, etc.) rather than
                # the lossy parsed dict alone.
                design_spec["_raw_document"] = arch_md
                print(f"  [Manifest] Injected _raw_document ({len(arch_md)} chars) into design_spec")
            except FileNotFoundError:
                design_spec = {
                    "title": f"{project_name} Architecture",
                    "summary": "Architecture design (artifact missing — placeholder)",
                    "components": [],
                    "technology_choices_list": [],
                    "approved": True,
                }

    # When resuming from stage 2 itself, load the architecture artifact for
    # the pipeline node to continue from (it may re-run architecture or read it)
    if resume_from == 2:
        arch_rel = _find_arch_path(manifest, base_dir)
        if arch_rel:
            try:
                arch_md = load_artifact(arch_rel, base_dir)
                design_spec = _parse_design_spec(arch_md)
                design_spec["_raw_document"] = arch_md
            except FileNotFoundError:
                pass

    # --- 6. Determine branch from manifest git metadata ---------------------
    git_info = manifest.get("git", {})
    branch: str = git_info.get("branch", "main")

    # --- 7. Determine pipeline path -----------------------------------------
    # Count completed stages to infer path; default to "full"
    stages = manifest.get("stages", [])
    completed_count = sum(1 for s in stages if s.get("status") == "completed")
    if completed_count <= 1:
        inferred_path = "lightweight"
    elif completed_count <= 3:
        inferred_path = "medium"
    else:
        inferred_path = "full"

    # --- 8. Assemble PipelineState ------------------------------------------
    effective_run_id = run_id if run_id else str(uuid.uuid4())
    effective_path = path if path else inferred_path
    state: PipelineState = {
        # Identity
        "run_id": effective_run_id,
        "request": request,
        "project_name": project_name,
        "branch": branch,
        "path": effective_path,
        "current_stage": current_stage,
        "target_dir": target_dir or "",
        # Stage artifacts
        "context_bundle": context_bundle,
        "strategic_brief": strategic_brief,
        "design_spec": design_spec,
        "task_plan": None,
        "workspace_info": None,
        "build_results": [],
        "verify_record": None,
        "review_result": None,
        "ship_result": None,
        "evolve_report": None,
        # Optional
        "changed_files": None,
        # Control flow
        "attempt_counts": {},
        "human_approvals": {},
        "cost_total_usd": 0.0,
        "errors": [],
        "messages": [],
    }

    return state
