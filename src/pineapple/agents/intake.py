"""Stage 0: Intake — classify request, load context, route path."""

import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

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


def _scan_codebase(target_dir) -> dict:
    """Scan a project directory for structure, tech stack, and README.

    Returns a dict with: tech_stack, file_counts, directories,
    readme_excerpt, existing_patterns.

    Args:
        target_dir: Path or str to the project directory.
    """
    summary: dict = {
        "tech_stack": [],
        "file_counts": {},
        "directories": [],
        "readme_excerpt": "",
        "existing_patterns": [],
    }

    if target_dir is None:
        return summary
    target_dir = Path(target_dir) if not isinstance(target_dir, Path) else target_dir

    if not target_dir.is_dir():
        return summary

    # 1. List top-level directories (skip hidden/venv).
    skip_dirs = {"node_modules", ".git", "__pycache__", ".venv", "venv", ".tox", ".mypy_cache"}
    top_dirs = sorted(
        d.name
        for d in target_dir.iterdir()
        if d.is_dir() and d.name not in skip_dirs and not d.name.startswith(".")
    )
    summary["directories"] = top_dirs

    # Infer patterns from directory names.
    dir_set = set(top_dirs)
    if "src" in dir_set:
        summary["existing_patterns"].append("src-layout")
    if "tests" in dir_set or "test" in dir_set:
        summary["existing_patterns"].append("has-tests")
    if "docs" in dir_set:
        summary["existing_patterns"].append("has-docs")

    # 2. Count files by extension (walk, but cap depth to avoid huge repos).
    ext_counter: Counter = Counter()
    file_count = 0
    max_files = 5000  # safety cap
    for root_str, dirs, files in os.walk(str(target_dir)):
        # Skip hidden and venv directories at every level.
        dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext:
                ext_counter[ext] += 1
            file_count += 1
            if file_count >= max_files:
                break
        if file_count >= max_files:
            break
    summary["file_counts"] = dict(ext_counter.most_common(15))

    # 3. Detect tech stack from package files.
    tech_stack = []
    pyproject = target_dir / "pyproject.toml"
    if pyproject.is_file():
        tech_stack.append("python (pyproject.toml)")
        try:
            content = pyproject.read_text(encoding="utf-8", errors="replace")
            if "fastapi" in content.lower():
                tech_stack.append("fastapi")
            if "django" in content.lower():
                tech_stack.append("django")
            if "flask" in content.lower():
                tech_stack.append("flask")
            if "pytest" in content.lower():
                tech_stack.append("pytest")
        except OSError:
            pass

    setup_py = target_dir / "setup.py"
    if setup_py.is_file() and "python" not in " ".join(tech_stack):
        tech_stack.append("python (setup.py)")

    pkg_json = target_dir / "package.json"
    if pkg_json.is_file():
        tech_stack.append("node (package.json)")
        try:
            content = pkg_json.read_text(encoding="utf-8", errors="replace")
            if '"react"' in content:
                tech_stack.append("react")
            if '"next"' in content or '"nextjs"' in content:
                tech_stack.append("nextjs")
            if '"typescript"' in content:
                tech_stack.append("typescript")
        except OSError:
            pass

    cargo_toml = target_dir / "Cargo.toml"
    if cargo_toml.is_file():
        tech_stack.append("rust (Cargo.toml)")

    go_mod = target_dir / "go.mod"
    if go_mod.is_file():
        tech_stack.append("go (go.mod)")

    # Fallback: check one level deep for monorepo/subdirectory layouts.
    if not tech_stack:
        for sub in target_dir.iterdir():
            if not sub.is_dir() or sub.name.startswith(".") or sub.name in skip_dirs:
                continue
            sub_pyproject = sub / "pyproject.toml"
            if sub_pyproject.is_file() and "python" not in " ".join(tech_stack):
                tech_stack.append(f"python ({sub.name}/pyproject.toml)")
            sub_setup = sub / "setup.py"
            if sub_setup.is_file() and "python" not in " ".join(tech_stack):
                tech_stack.append(f"python ({sub.name}/setup.py)")
            sub_pkg = sub / "package.json"
            if sub_pkg.is_file() and "node" not in " ".join(tech_stack):
                tech_stack.append(f"node ({sub.name}/package.json)")
            sub_cargo = sub / "Cargo.toml"
            if sub_cargo.is_file() and "rust" not in " ".join(tech_stack):
                tech_stack.append(f"rust ({sub.name}/Cargo.toml)")

    # Last resort: infer from dominant file extensions.
    if not tech_stack:
        fc = summary["file_counts"]
        if fc.get(".py", 0) >= 5:
            tech_stack.append("python (inferred)")
        if fc.get(".ts", 0) + fc.get(".tsx", 0) + fc.get(".js", 0) + fc.get(".jsx", 0) >= 5:
            tech_stack.append("javascript/typescript (inferred)")
        if fc.get(".rs", 0) >= 5:
            tech_stack.append("rust (inferred)")
        if fc.get(".go", 0) >= 5:
            tech_stack.append("go (inferred)")

    summary["tech_stack"] = tech_stack

    # 4. Read README excerpt (first 500 chars).
    for readme_name in ("README.md", "README.rst", "README.txt", "README"):
        readme = target_dir / readme_name
        if readme.is_file():
            try:
                text = readme.read_text(encoding="utf-8", errors="replace")
                summary["readme_excerpt"] = text[:500]
            except OSError:
                pass
            break

    # 5. Read CLAUDE.md if present.
    claude_md = target_dir / "CLAUDE.md"
    if claude_md.is_file():
        summary["existing_patterns"].append("has-CLAUDE.md")

    return summary


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


def _load_project_memory(target_dir: str = None) -> dict:
    """Load project memory from standard locations.

    Searches for MEMORY.md, decisions.md, and project bibles in the target
    directory, CWD, and ~/.claude/projects/ subdirectories.

    Returns a dict with keys: memory_sources, locked_decisions,
    project_state, user_preferences.
    """
    memory: dict = {
        "memory_sources": [],
        "locked_decisions": [],
        "project_state": {},
        "user_preferences": {},
    }

    # Search paths (in priority order).
    search_paths: list[Path] = []
    if target_dir:
        search_paths.append(Path(target_dir))
    cwd = Path.cwd()
    if not target_dir or Path(target_dir).resolve() != cwd.resolve():
        search_paths.append(cwd)

    # Also check ~/.claude/projects/ for memory files.
    home_claude = Path.home() / ".claude" / "projects"
    if home_claude.exists():
        for project_dir in sorted(home_claude.iterdir()):
            if project_dir.is_dir():
                memory_file = project_dir / "memory" / "MEMORY.md"
                if memory_file.exists():
                    try:
                        content = memory_file.read_text(
                            encoding="utf-8", errors="replace"
                        )
                        memory["memory_sources"].append(str(memory_file))
                        _extract_memory_sections(content, memory)
                        print(f"  [Intake] Loaded memory: {memory_file}")
                    except OSError:
                        pass

    # Check for decisions.md in search paths.
    for sp in search_paths:
        decisions = sp / "decisions.md"
        if decisions.exists():
            try:
                content = decisions.read_text(
                    encoding="utf-8", errors="replace"
                )
                memory["locked_decisions"].append(content[:2000])
                memory["memory_sources"].append(str(decisions))
                print(f"  [Intake] Loaded decisions: {decisions}")
            except OSError:
                pass

    # Check for project bibles in search paths.
    for sp in search_paths:
        projects_dir = sp / "projects"
        if projects_dir.is_dir():
            for bible in sorted(projects_dir.glob("*.yaml")):
                if bible.is_file():
                    try:
                        content = bible.read_text(
                            encoding="utf-8", errors="replace"
                        )
                        memory["project_state"][bible.name] = content[:1000]
                        print(f"  [Intake] Loaded project bible: {bible}")
                    except OSError:
                        pass

    if memory["memory_sources"]:
        print(
            f"  [Intake] Project memory loaded from "
            f"{len(memory['memory_sources'])} source(s)."
        )
    else:
        print("  [Intake] No project memory found.")

    return memory


def _extract_memory_sections(content: str, memory: dict) -> None:
    """Parse a MEMORY.md file and extract key sections into the memory dict."""
    lines = content.splitlines()
    current_section = ""
    section_lines: list[str] = []

    for line in lines:
        if line.startswith("## "):
            # Flush previous section.
            if current_section and section_lines:
                _store_section(current_section, section_lines, memory)
            current_section = line[3:].strip().lower()
            section_lines = []
        elif current_section:
            section_lines.append(line)

    # Flush last section.
    if current_section and section_lines:
        _store_section(current_section, section_lines, memory)


def _store_section(
    section_name: str, lines: list[str], memory: dict
) -> None:
    """Store a parsed MEMORY.md section into the appropriate memory bucket."""
    text = "\n".join(lines).strip()
    if not text:
        return

    # Cap each section at 1500 chars to keep memory compact.
    text = text[:1500]

    if "decision" in section_name:
        memory["locked_decisions"].append(text)
    elif "preference" in section_name or "tool" in section_name:
        memory["user_preferences"][section_name] = text
    elif "active project" in section_name:
        memory["project_state"]["_active_projects"] = text
    elif "architecture" in section_name:
        memory["locked_decisions"].append(f"[{section_name}] {text}")
    elif "rule" in section_name or "protocol" in section_name:
        memory["user_preferences"][section_name] = text


def _load_context_files(target_dir: Optional[Path] = None) -> list[str]:
    """Scan for known context files in target_dir (falling back to CWD).

    Returns a list of absolute paths that exist and were loaded.
    """
    base = target_dir if target_dir and target_dir.is_dir() else Path.cwd()
    found: list[str] = []

    # Individual files.
    for name in _CONTEXT_FILENAMES:
        path = base / name
        if path.is_file():
            found.append(str(path))
            print(f"  [Intake] Found context file: {path}")

    # Directories — collect YAML files inside them.
    for dirname in _CONTEXT_DIRS:
        dirpath = base / dirname
        if dirpath.is_dir():
            for child in sorted(dirpath.iterdir()):
                if child.suffix in {".yaml", ".yml"} and child.is_file():
                    found.append(str(child))
                    print(f"  [Intake] Found project bible: {child}")

    if not found:
        print(f"  [Intake] No context files found in {base}.")

    return found


def _search_similar_projects(request: str, project_name: str) -> list[dict]:
    """Search ChromaDB for similar past projects.

    Returns a list of dicts with keys: project, request, summary, score.
    Returns empty list if ChromaDB is not available or no matches found.
    """
    try:
        import chromadb
    except ImportError:
        return []

    db_path = os.path.join(".pineapple", "chromadb")
    try:
        client = chromadb.PersistentClient(path=db_path)
        collection = client.get_or_create_collection(
            name="project_history",
            metadata={"description": "Past pipeline project specs and outcomes"},
        )

        # If collection is empty, nothing to search
        if collection.count() == 0:
            print("  [Intake] ChromaDB: No past projects stored yet")
            return []

        # Search for similar projects
        results = collection.query(
            query_texts=[request],
            n_results=min(3, collection.count()),
        )

        similar = []
        if results and results["documents"]:
            for i, doc in enumerate(results["documents"][0]):
                meta = results["metadatas"][0][i] if results["metadatas"] else {}
                distance = results["distances"][0][i] if results["distances"] else 0
                similar.append({
                    "project": meta.get("project", "unknown"),
                    "request": meta.get("request", ""),
                    "summary": doc[:200],
                    "score": round(1 - distance, 3) if distance else 0,
                })
            print(f"  [Intake] ChromaDB: Found {len(similar)} similar past project(s)")

        return similar
    except Exception as exc:
        print(f"  [Intake] ChromaDB: Search failed — {exc}")
        return []


def store_project_in_chromadb(project_name: str, request: str, summary: str, design_spec: dict = None) -> bool:
    """Store a completed project in ChromaDB for future similarity search.

    Called by the evolver after a successful pipeline run.
    Returns True if stored successfully.
    """
    try:
        import chromadb
    except ImportError:
        return False

    db_path = os.path.join(".pineapple", "chromadb")
    try:
        client = chromadb.PersistentClient(path=db_path)
        collection = client.get_or_create_collection(name="project_history")

        # Build document from available info
        doc_parts = [f"Project: {project_name}", f"Request: {request}"]
        if summary:
            doc_parts.append(f"Summary: {summary}")
        if design_spec:
            components = design_spec.get("components", [])
            if components:
                comp_names = [c.get("name", "") for c in components]
                doc_parts.append(f"Components: {', '.join(comp_names)}")

        document = "\n".join(doc_parts)

        collection.upsert(
            ids=[project_name],
            documents=[document],
            metadatas=[{"project": project_name, "request": request}],
        )
        return True
    except Exception:
        return False


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

    # 0. Resolve target directory.
    raw_target: str = state.get("target_dir", "") or ""
    target_dir: Optional[Path] = None
    if raw_target:
        candidate = Path(raw_target).resolve()
        if candidate.is_dir():
            target_dir = candidate
            print(f"  [Intake] Target directory: {target_dir}")
        else:
            print(f"  [Intake] WARNING: --target-dir {raw_target!r} is not a valid directory, falling back to CWD")

    # 1. Extract project name (respect existing value).
    project_name: str = state.get("project_name") or _slugify(request)
    print(f"  [Intake] Project name: {project_name}")

    # 2. Classify the request.
    project_type, classification = _classify_request(request)
    print(f"  [Intake] Classification: {project_type} ({classification})")

    # 3. Determine routing path.
    path = _determine_path(project_type, user_path)
    print(f"  [Intake] Path: {path}" + (" (user-specified)" if user_path else " (auto)"))

    # 4. Load context files from target dir (or CWD).
    context_files = _load_context_files(target_dir)

    # 5. Load project memory (MEMORY.md, decisions.md, project bibles).
    project_memory = _load_project_memory(
        str(target_dir) if target_dir else None
    )

    # 6. Scan codebase structure.
    scan_dir = target_dir if target_dir else Path.cwd()
    codebase_summary = _scan_codebase(scan_dir)
    if codebase_summary.get("tech_stack"):
        print(f"  [Intake] Tech stack: {', '.join(codebase_summary['tech_stack'])}")
    if codebase_summary.get("directories"):
        print(f"  [Intake] Top-level dirs: {', '.join(codebase_summary['directories'][:10])}")
    file_total = sum(codebase_summary.get("file_counts", {}).values())
    if file_total:
        print(f"  [Intake] Files scanned: {file_total}")

    # 6b. Search for similar past projects in ChromaDB.
    similar_projects = _search_similar_projects(request, project_name)
    if similar_projects:
        print(f"  [Intake] Similar projects: {', '.join(s['project'] for s in similar_projects)}")

    # 7. Build the ContextBundle artifact.
    bundle = ContextBundle(
        project_type=project_type,
        context_files=context_files,
        classification=classification,
        codebase_summary=codebase_summary,
        project_memory=project_memory,
        loaded_at=datetime.now(timezone.utc),
        similar_projects=similar_projects,
    )

    mem_sources = len(project_memory.get("memory_sources", []))
    print(
        f"  [Intake] Context bundle created with {len(context_files)} file(s)"
        f" and {mem_sources} memory source(s)."
    )

    # 8. Return state update.
    result: dict = {
        "current_stage": "intake",
        "context_bundle": bundle.model_dump(),
        "project_name": project_name,
    }

    # Only set path if it wasn't already provided by the user.
    if not user_path:
        result["path"] = path

    return result
