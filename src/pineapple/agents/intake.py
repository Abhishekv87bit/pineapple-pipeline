"""Stage 0: Intake — classify request, load context, route path."""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path

from pineapple.models import ContextBundle
from pineapple.state import PipelineState

# Keywords that signal each project type / path.
_BUG_KEYWORDS = {"fix", "bug", "patch", "hotfix", "broken", "crash", "error"}
_FEATURE_KEYWORDS = {"add", "feature", "implement", "create", "build", "new"}

# Context files we look for relative to CWD.
_CONTEXT_FILENAMES = [
    "CLAUDE.md",
    "MEMORY.md",
]
_CONTEXT_DIRS = [
    "projects",
]


def _slugify(text: str, max_words: int = 5) -> str:
    """Turn a free-text request into a short slug suitable for a project name."""
    # Strip non-alphanumeric (keep spaces/hyphens), lowercase, take first N words.
    cleaned = re.sub(r"[^a-zA-Z0-9\s-]", "", text).strip().lower()
    words = cleaned.split()[:max_words]
    slug = "-".join(words) if words else "unnamed-project"
    # Cap length at 60 chars.
    return slug[:60]


def _classify_request(request: str) -> tuple[str, str]:
    """Return (project_type, classification_reason) based on keyword analysis.

    project_type is one of: "bug_fix", "new_feature", "new_project".
    """
    lower = request.lower()
    tokens = set(re.findall(r"[a-z]+", lower))

    bug_hits = tokens & _BUG_KEYWORDS
    feature_hits = tokens & _FEATURE_KEYWORDS

    if bug_hits:
        return (
            "bug_fix",
            f"Matched bug-fix keywords: {', '.join(sorted(bug_hits))}",
        )

    if feature_hits:
        # Heuristic: "create" / "build" + "new" → new_project, otherwise new_feature.
        if {"create", "build", "new"} & feature_hits:
            return (
                "new_project",
                f"Matched new-project keywords: {', '.join(sorted(feature_hits))}",
            )
        return (
            "new_feature",
            f"Matched feature keywords: {', '.join(sorted(feature_hits))}",
        )

    return ("new_project", "No strong keyword signal — defaulting to new_project")


def _determine_path(
    project_type: str,
    user_path: str | None,
) -> str:
    """Decide the routing path (full / medium / lightweight).

    If the user already chose a path via CLI, respect it.
    Otherwise infer from the project type.
    """
    if user_path:
        return user_path

    if project_type == "bug_fix":
        return "lightweight"
    if project_type == "new_feature":
        return "medium"
    return "full"


def _load_context_files() -> list[str]:
    """Scan the current working directory for known context files.

    Returns a list of absolute paths that exist and were loaded.
    """
    cwd = Path.cwd()
    found: list[str] = []

    # Individual files.
    for name in _CONTEXT_FILENAMES:
        path = cwd / name
        if path.is_file():
            found.append(str(path))
            print(f"  [Intake] Found context file: {path}")

    # Directories — collect YAML files inside them.
    for dirname in _CONTEXT_DIRS:
        dirpath = cwd / dirname
        if dirpath.is_dir():
            for child in sorted(dirpath.iterdir()):
                if child.suffix in {".yaml", ".yml"} and child.is_file():
                    found.append(str(child))
                    print(f"  [Intake] Found project bible: {child}")

    if not found:
        print("  [Intake] No context files found in working directory.")

    return found


# ---------------------------------------------------------------------------
# Public node
# ---------------------------------------------------------------------------


def intake_node(state: PipelineState) -> dict:
    """Process intake: classify request, load context, determine path.

    This is a pure-Python node — no LLM calls.
    """
    request: str = state.get("request", "")
    user_path: str | None = state.get("path")

    print(f"[Stage 0: Intake] Processing: {request!r}")

    # 1. Extract project name (respect existing value).
    project_name: str = state.get("project_name") or _slugify(request)
    print(f"  [Intake] Project name: {project_name}")

    # 2. Classify the request.
    project_type, classification = _classify_request(request)
    print(f"  [Intake] Classification: {project_type} ({classification})")

    # 3. Determine routing path.
    path = _determine_path(project_type, user_path)
    print(f"  [Intake] Path: {path}" + (" (user-specified)" if user_path else " (auto)"))

    # 4. Load context files.
    context_files = _load_context_files()

    # 5. Build the ContextBundle artifact.
    bundle = ContextBundle(
        project_type=project_type,
        context_files=context_files,
        classification=classification,
        loaded_at=datetime.now(timezone.utc),
    )

    print(f"  [Intake] Context bundle created with {len(context_files)} file(s).")

    # 6. Return state update.
    result: dict = {
        "current_stage": "intake",
        "context_bundle": bundle.model_dump(),
        "project_name": project_name,
    }

    # Only set path if it wasn't already provided by the user.
    if not user_path:
        result["path"] = path

    return result
