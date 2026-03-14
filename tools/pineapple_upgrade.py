"""
pineapple upgrade -- Compare and update project templates.

Usage: python production-pipeline/tools/pineapple_upgrade.py <project-path> [--dry-run] [--json]

Compares version headers in project files against current templates.
Shows which templates are outdated and what changed.
"""
from __future__ import annotations
import difflib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"

# Map: template name -> relative destination in project
TEMPLATE_DESTINATIONS = {
    "rate_limiter.py": "{backend}/app/middleware/rate_limiter.py",
    "input_guardrails.py": "{backend}/app/middleware/input_guardrails.py",
    "observability.py": "{backend}/app/middleware/observability.py",
    "resilience.py": "{backend}/app/middleware/resilience.py",
    "cache.py": "{backend}/app/middleware/cache.py",
    "mcp_server.py": "{backend}/mcp_server.py",
    "test_adversarial.py": "{backend}/tests/test_adversarial.py",
    "test_eval_benchmark.py": "{backend}/tests/test_eval_benchmark.py",
    "Dockerfile.fastapi": "{backend}/Dockerfile",
    "Dockerfile.vite": "{frontend}/Dockerfile",
    "docker-compose.template.yml": "docker-compose.yml",
    "ci.github-actions.yml": ".github/workflows/ci.yml",
    "env.template": ".env.example",
}

@dataclass
class TemplateStatus:
    template_name: str
    project_file: str
    status: Literal["current", "outdated", "missing", "custom"]
    current_version: str = ""
    project_version: str = ""
    diff_lines: int = 0

@dataclass
class UpgradeReport:
    templates: list[TemplateStatus] = field(default_factory=list)

    @property
    def outdated_count(self) -> int:
        return sum(1 for t in self.templates if t.status == "outdated")

    def to_dict(self) -> dict:
        return {
            "total_templates": len(self.templates),
            "outdated": self.outdated_count,
            "templates": [
                {"name": t.template_name, "project_file": t.project_file,
                 "status": t.status, "current_version": t.current_version,
                 "project_version": t.project_version, "diff_lines": t.diff_lines}
                for t in self.templates
            ],
        }

def _extract_version(text: str) -> str:
    """Extract pipeline version from file header."""
    match = re.search(r"Pineapple Pipeline v([\d.]+)", text)
    return match.group(1) if match else ""

def _detect_backend(project_path: Path) -> str:
    for candidate in ["backend", "server", "api", "src"]:
        if (project_path / candidate).is_dir():
            return candidate
    return "backend"

def _detect_frontend(project_path: Path) -> str:
    for candidate in ["frontend", "client", "web", "ui"]:
        if (project_path / candidate).is_dir():
            return candidate
    return "frontend"

def check_templates(project_path: Path) -> UpgradeReport:
    """Check all templates for updates."""
    report = UpgradeReport()
    backend = _detect_backend(project_path)
    frontend = _detect_frontend(project_path)

    for template_name, dest_pattern in TEMPLATE_DESTINATIONS.items():
        template_path = TEMPLATE_DIR / template_name
        if not template_path.is_file():
            continue

        dest_rel = dest_pattern.format(backend=backend, frontend=frontend)
        project_file = project_path / dest_rel

        template_text = template_path.read_text(encoding="utf-8")
        current_version = _extract_version(template_text)

        if not project_file.is_file():
            report.templates.append(TemplateStatus(
                template_name=template_name,
                project_file=dest_rel,
                status="missing",
                current_version=current_version,
            ))
            continue

        project_text = project_file.read_text(encoding="utf-8")
        project_version = _extract_version(project_text)

        if not project_version:
            # No version header = custom file, don't touch
            report.templates.append(TemplateStatus(
                template_name=template_name,
                project_file=dest_rel,
                status="custom",
                current_version=current_version,
            ))
            continue

        # Compare versions
        if project_version == current_version:
            report.templates.append(TemplateStatus(
                template_name=template_name,
                project_file=dest_rel,
                status="current",
                current_version=current_version,
                project_version=project_version,
            ))
        else:
            # Count diff lines
            diff = list(difflib.unified_diff(
                project_text.splitlines(), template_text.splitlines(),
                lineterm="",
            ))
            report.templates.append(TemplateStatus(
                template_name=template_name,
                project_file=dest_rel,
                status="outdated",
                current_version=current_version,
                project_version=project_version,
                diff_lines=len([l for l in diff if l.startswith("+") or l.startswith("-")]),
            ))

    return report

def show_diff(project_path: Path, template_name: str) -> str:
    """Show unified diff between project file and current template."""
    backend = _detect_backend(project_path)
    frontend = _detect_frontend(project_path)

    dest_pattern = TEMPLATE_DESTINATIONS.get(template_name)
    if not dest_pattern:
        return f"Unknown template: {template_name}"

    dest_rel = dest_pattern.format(backend=backend, frontend=frontend)
    project_file = project_path / dest_rel
    template_file = TEMPLATE_DIR / template_name

    if not project_file.is_file():
        return f"Project file not found: {dest_rel}"
    if not template_file.is_file():
        return f"Template not found: {template_name}"

    project_text = project_file.read_text(encoding="utf-8")
    template_text = template_file.read_text(encoding="utf-8")

    diff = difflib.unified_diff(
        project_text.splitlines(keepends=True),
        template_text.splitlines(keepends=True),
        fromfile=f"project/{dest_rel}",
        tofile=f"template/{template_name}",
    )
    return "".join(diff)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Pineapple Pipeline Template Upgrade Check")
    parser.add_argument("project_path", type=Path, help="Project path to check")
    parser.add_argument("--diff", help="Show diff for a specific template")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.diff:
        print(show_diff(args.project_path, args.diff))
        sys.exit(0)

    report = check_templates(args.project_path)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(f"\nPineapple Upgrade Check v1.0.0")
        print(f"================================")
        for t in report.templates:
            tag = {"current": "  OK  ", "outdated": "UPDATE", "missing": " NEW  ", "custom": " SKIP "}[t.status]
            print(f"  [{tag}] {t.template_name}")
            if t.status == "outdated":
                print(f"         v{t.project_version} -> v{t.current_version} ({t.diff_lines} lines changed)")
            elif t.status == "missing":
                print(f"         Not in project (run apply_pipeline.py to add)")
            elif t.status == "custom":
                print(f"         Custom file (no version header, skipped)")

        if report.outdated_count > 0:
            print(f"\n{report.outdated_count} template(s) need updating.")
            print(f"Use --diff <template> to see changes.")
        else:
            print(f"\nAll templates up to date.")

    sys.exit(0)
